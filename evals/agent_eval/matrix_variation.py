"""How much the response matrix varies between runs of the same code: run it on several
matrix CSVs and it reports, per line, how often ALICE used the intended tool and whether
she picked the same tool every time.

Without this, one run can't tell a real change from noise: two runs of identical code on
2026-10-17 flipped five lines each way. Measure unchanged runs first (the baseline), then
judge a change against that spread.

Usage:
    .venv/bin/python evals/agent_eval/matrix_variation.py results/response-matrix__A.csv results/...B.csv ...
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_cases import on_script  # noqa: E402


def _key(row: dict) -> tuple:
    return row["Scenario"], row["Conversation"], row["Turn"], row["Pilot says"]


def main(paths: list[str]) -> int:
    runs = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as handle:
            runs.append(list(csv.DictReader(handle)))

    print(f"{len(runs)} runs")
    per_run_rates = []
    for path, rows in zip(paths, runs, strict=True):
        ok = judged = 0
        seconds = []
        for row in rows:
            verdict = on_script(row["Intended tool"], row["Tool ALICE called"])
            if verdict is not None:
                judged += 1
                ok += verdict
            if row["Seconds (no speech)"]:
                seconds.append(float(row["Seconds (no speech)"]))
        rate = 100 * ok / judged if judged else 0.0
        per_run_rates.append(rate)
        median_seconds = statistics.median(seconds) if seconds else 0.0
        print(f"  {Path(path).name}: intended tool {ok}/{judged} ({rate:.0f}%), median {median_seconds:.1f} s")
    if len(per_run_rates) > 1:
        print(f"  spread: {min(per_run_rates):.0f}-{max(per_run_rates):.0f}%, "
              f"stdev {statistics.stdev(per_run_rates):.1f} points")

    verdicts = defaultdict(list)
    tools = defaultdict(list)
    intended = {}
    for rows in runs:
        for row in rows:
            key = _key(row)
            intended[key] = row["Intended tool"]
            verdicts[key].append(on_script(row["Intended tool"], row["Tool ALICE called"]))
            tools[key].append(row["Tool ALICE called"] or "(none)")

    always, never, flaky, unjudged = [], [], [], []
    for key, results in verdicts.items():
        judged = []
        for result in results:
            if result is not None:
                judged.append(result)
        if not judged:
            unjudged.append(key)
        elif all(judged):
            always.append(key)
        elif not any(judged):
            never.append(key)
        else:
            flaky.append(key)

    print(f"\nlines: {len(always)} always right, {len(never)} always wrong, {len(flaky)} flaky, "
          f"{len(unjudged)} not judged (gaps)")

    def show(title: str, keys: list) -> None:
        print(f"\n{title}")
        for key in sorted(keys):
            scenario, conversation, turn, says = key
            where = f"{conversation[:20]} #{turn}" if conversation else scenario[:22]
            right = sum(1 for verdict in verdicts[key] if verdict)
            choices = ", ".join(sorted(set(tools[key])))
            tally = f"{right}/{len(verdicts[key])}"
            print(f"  {tally}  {where:24} {says[:40]:40} want {intended[key][:26]:26} got {choices[:60]}")

    show("Flaky (right on some runs, wrong on others):", flaky)
    show("Always wrong:", never)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

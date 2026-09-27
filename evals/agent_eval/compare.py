"""Diff two agent_eval report files — the "are we making measurable progress" view.
Per-case pass/fail movement, plus overall pass-rate/cost/token/latency deltas.

Usage:
    cd app && PYTHONPATH=. ../.venv/bin/python ../evals/agent_eval/compare.py \\
        ../evals/agent_eval/results/baseline.json ../evals/agent_eval/results/candidate.json
"""

import json
import sys
from pathlib import Path


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _key(record: dict) -> tuple:
    return (record["case_id"], record["phrasing"])


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: compare.py <baseline.json> <candidate.json>")
        return 1

    baseline = _load(sys.argv[1])
    candidate = _load(sys.argv[2])

    base_by_key = {_key(r): r for r in baseline["records"]}
    cand_by_key = {_key(r): r for r in candidate["records"]}

    print(f"Baseline:  {baseline['summary']['model_provider']}:{baseline['summary']['model_name']}")
    print(f"Candidate: {candidate['summary']['model_provider']}:{candidate['summary']['model_name']}\n")

    for key in sorted(base_by_key.keys() | cand_by_key.keys()):
        base = base_by_key.get(key)
        cand = cand_by_key.get(key)
        case_id, phrasing = key
        if base is None:
            print(f"  [NEW]         {case_id!r} — {phrasing!r} (only in candidate)")
            continue
        if cand is None:
            print(f"  [REMOVED]     {case_id!r} — {phrasing!r} (only in baseline)")
            continue

        base_pass, cand_pass = base["overall_pass"], cand["overall_pass"]
        if base_pass == cand_pass:
            marker = "same" if base_pass else "still failing"
        elif cand_pass:
            marker = "FIXED"
        else:
            marker = "REGRESSED"
        print(f"  [{marker:^13}] {case_id!r} — {phrasing!r}")

    base_summary, cand_summary = baseline["summary"], candidate["summary"]
    print("\n=== Overall ===")
    print(f"  pass rate:    {base_summary['pass_rate']:.0%} -> {cand_summary['pass_rate']:.0%}")
    print(f"  total cost:   ${base_summary['total_cost_usd']} -> ${cand_summary['total_cost_usd']}")
    print(f"  total tokens: {base_summary['total_tokens']} -> {cand_summary['total_tokens']}")
    print(f"  avg latency:  {base_summary['avg_latency_ms']}ms -> {cand_summary['avg_latency_ms']}ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Runs evals/agent_eval's harness across a batch of model configs on this machine,
then commits + pushes the resulting report files back to origin. Meant for a
benchmark machine that isn't where the main dev/git history lives (e.g. a 5090 PC) --
run this there, then `git pull` on your usual machine to see the results.

Each config runs run.py in its own subprocess, matching its own "one run = one
model, driven by env vars" design exactly (graph.py builds its models once at
import time, so looping configs in a single process wouldn't pick up a changed env
var without reimporting -- a subprocess per config sidesteps that cleanly).

One-time setup this script does NOT do: cloning the repo, creating .env, the Python
venv, installing Ollama itself. That's still the manual checklist -- this only
covers "pull whatever models this batch needs, run the harness for each config,
push the results."

Usage:
    ../.venv/bin/python evals/agent_eval/run_batch.py
Edit CONFIGS below to choose what runs in a given batch.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_DIR = REPO_ROOT / "app"
RUN_SCRIPT = REPO_ROOT / "evals" / "agent_eval" / "run.py"

# Judge stays fixed across the whole batch -- every run in one batch needs to be
# graded the same way to be comparable to every other, and to earlier batches from
# other machines once real API credits/keys make the intended openai/gpt-4o-mini
# judge usable again (see judge.py's own default).
JUDGE_ENV = {"HARNESS_JUDGE_PROVIDER": "ollama", "HARNESS_JUDGE_MODEL": "mistral-small:latest"}

# (label for the commit message, env var overrides for that run). Add/remove entries
# to choose what a given batch covers -- nothing else in this file needs editing.
CONFIGS: list[tuple[str, dict[str, str]]] = [
    ("gemma4 8B", {"OLLAMA_CHAT_MODEL": "gemma4"}),
    ("gemma4 12B", {"OLLAMA_CHAT_MODEL": "gemma4:12b"}),
    ("split 8B-classify/12B-respond", {
        "OLLAMA_CHAT_MODEL": "gemma4:12b",
        "OLLAMA_CLASSIFICATION_MODEL": "gemma4",
    }),
]


def _ollama_pull(model: str) -> None:
    print(f"ollama pull {model} ...")
    subprocess.run(["ollama", "pull", model], check=True)


def _models_needed() -> set[str]:
    models = {JUDGE_ENV["HARNESS_JUDGE_MODEL"]}
    for _, overrides in CONFIGS:
        for key in ("OLLAMA_CHAT_MODEL", "OLLAMA_CLASSIFICATION_MODEL"):
            if key in overrides:
                models.add(overrides[key])
    return models


def _run_one_config(label: str, overrides: dict[str, str]) -> bool:
    print(f"\n{'=' * 60}\nRunning: {label}\n{'=' * 60}")
    env = {**os.environ, **JUDGE_ENV, **overrides, "PYTHONPATH": "."}
    result = subprocess.run([sys.executable, str(RUN_SCRIPT)], cwd=str(APP_DIR), env=env)
    # run.py's own exit code is 0 (all cases passed) or 1 (some failed) -- neither
    # means the run itself crashed. Anything else means it genuinely errored out.
    if result.returncode not in (0, 1):
        print(f"WARNING: {label!r} exited {result.returncode} -- likely crashed, not just failed cases.")
        return False
    return True


def _commit_and_push(labels: list[str]) -> None:
    subprocess.run(["git", "add", "evals/agent_eval/results/"], cwd=str(REPO_ROOT), check=True)

    already_clean = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=str(REPO_ROOT),
    ).returncode == 0
    if already_clean:
        print("\nNo new report files to commit.")
        return

    message = "Add benchmark runs from remote machine: " + ", ".join(labels)
    subprocess.run(["git", "commit", "-m", message], cwd=str(REPO_ROOT), check=True)
    subprocess.run(["git", "push"], cwd=str(REPO_ROOT), check=True)
    print(f"\nPushed: {message}")


def main() -> int:
    for model in sorted(_models_needed()):
        _ollama_pull(model)

    completed_labels = []
    for label, overrides in CONFIGS:
        if _run_one_config(label, overrides):
            completed_labels.append(label)

    if not completed_labels:
        print("\nNothing completed successfully -- skipping commit/push.")
        return 1

    _commit_and_push(completed_labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())

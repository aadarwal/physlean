#!/usr/bin/env python3
"""Six-tier rerun driver (EPOCH2 32b integration, cluster CPU step).

Reads the committed six-tier completion ledger, rebuilds the analyzer
command lines for all eleven frozen consumer runs (five ladder, five
dose-budget, one k4x), executes them, then runs the expansion-
consistency verifier of each new artifact against its committed
five-tier predecessor. Pure orchestration: every substantive check
lives in the consumers themselves; this driver only fails loudly.
Run from the cluster worktree AFTER the ledger-v2 evidence commit and
BEFORE the epoch battery rebind."""
import json
import os
import subprocess
import sys

PY = ".venv/bin/python"
LEDGER = "results_v2/v2b/ladder/COMPLETION_LEDGER_V2.json"
REVEAL = ("results_v2/v2b/nll_exploratory_reveal/"
          "job20007464_nll_exploratory_reveal.json")
SAMPLE = "results_v2/v2b/sample/job19989076_sample.json"
POOL_CANDIDATES = ("/orcd/pool/008/aadarwal/physlean-nll-launch/"
                   "results_v2/v2b/candidates")
REPO_TASKS = (("mathlib4", 0), ("batteries", 1), ("physlib", 2),
              ("sympy", 3), ("astropy", 4))
BATTERY = {
    "q25c-0.5b": "results_v2/battery/battery_pilot_0p5b.json",
    "q25c-1.5b": "results_v2/battery/battery_pilot_1p5b.json",
    "q25c-3b": "results_v2/battery/battery_pilot_3b.json",
    "q25c-7b": "results_v2/battery/battery_pilot_7b.json",
    "q25c-14b": "results_v2/battery/battery_pilot_14b.json",
    "q25c-32b": "results_v2/battery/battery_pilot_32b.json",
}
STAMP = "20260810"
PRIOR = {
    ("ladder", repo): f"results_v2/v2b/ladder/ladder_20260809_{i}_{repo}"
                      f".json" for repo, i in REPO_TASKS}
PRIOR.update({
    ("budget", repo): f"results_v2/v2b/ladder/dose_budget_20260809_{i}_"
                      f"{repo}.json" for repo, i in REPO_TASKS})
PRIOR[("k4x", "physlib")] = \
    "results_v2/v2b/ladder/dose_k4x_20260809_2_physlib.json"


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main():
    with open(LEDGER) as handle:
        ledger = json.load(handle)
    repos = ledger["repos"]

    def common_args(repo, index):
        args = ["--repo", repo,
                "--manifest",
                f"results_v2/v2b/assembly/job19991210_{index}_{repo}.json",
                "--sample", SAMPLE,
                "--candidates",
                f"{POOL_CANDIDATES}/job19982184_{index}_{repo}.json",
                "--ledger", LEDGER, "--reveal", REVEAL]
        for tier, row in sorted(repos[repo].items()):
            args += ["--completion", f"{tier}={row['path']}"]
        for tier, path in sorted(BATTERY.items()):
            args += ["--battery", f"{tier}={path}"]
        return args

    outputs = []
    for repo, index in REPO_TASKS:
        out = f"results_v2/v2b/ladder/ladder_{STAMP}_{index}_{repo}.json"
        run([PY, "analyze_v2b_nll_ladder.py"] + common_args(repo, index)
            + ["--out", out])
        outputs.append((("ladder", repo), out))
    for repo, index in REPO_TASKS:
        out = (f"results_v2/v2b/ladder/dose_budget_{STAMP}_{index}_"
               f"{repo}.json")
        run([PY, "analyze_v2b_dose.py", "--mode", "budget"]
            + common_args(repo, index) + ["--out", out])
        outputs.append((("budget", repo), out))
    out = f"results_v2/v2b/ladder/dose_k4x_{STAMP}_2_physlib.json"
    run([PY, "analyze_v2b_dose.py", "--mode", "k4x"]
        + common_args("physlib", 2) + ["--out", out])
    outputs.append((("k4x", "physlib"), out))

    for key, current in outputs:
        verify_out = current.replace(".json", ".verify.json")
        run([PY, "verify_v2b_expansion_consistency.py",
             "--prior", PRIOR[key], "--current", current,
             "--out", verify_out])
    print(f"SIX-TIER-ANALYSES-DONE {len(outputs)} artifacts + verifies")
    return 0


if __name__ == "__main__":
    sys.exit(main())

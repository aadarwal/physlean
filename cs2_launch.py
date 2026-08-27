#!/usr/bin/env python3
"""ARM_CS CS-2 task-list generator + HP-walk driver (v1, ARM_CS §4).

Stages (each prints the tasks file + the sbatch submit line):
  hp             rung-1 grid: lr {3e-4,1e-3,3e-3} x epochs {1,2,4},
                 ctx 4096, seed 0 (9 tasks/lang).
  pick --frac F  scan results_cs/runs for rung-F seed-0 ctx-4096 runs,
                 pick min final_val_bpb per lang, write/update
                 results_cs/hp_incumbents.json[lang][frac] = {lr, epochs}.
  walk --frac F  6 tasks/lang at rung F: incumbent (from the PREVIOUS
                 rung's pick) + the five neighbors
                 {lr*3, lr, lr/3} x {ep, ep*2} minus the incumbent.
  ladder         7 rungs x T {512, 4096} x seeds {0,1,2} per lang, using
                 the per-rung incumbents json (ARM_CS v1: 3 seeds at
                 EVERY rung).
  capacity       one 30m run per lang at the top rung (submit with the
                 printed CS2_SIZE=30m line).

Tasks file line: "lang max_bytes ctx seed lr epochs tag".
"""
import argparse
import glob
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
FRACS = [1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0]
LANGS = ["lean", "python", "cpp", "latex"]
HP_LRS = [3e-4, 1e-3, 3e-3]
HP_EPOCHS = [1, 2, 4]
INCUMBENTS = os.path.join(BASE, "results_cs", "hp_incumbents.json")


def fkey(f):
    return f"{f:.6f}"


def boundaries(lang):
    man = json.load(open(os.path.join(BASE, "data", "cs2",
                                      f"{lang}_cs2.json")))
    return {float(k): v for k, v in man["rung_boundaries"].items()}


def load_incumbents():
    if os.path.exists(INCUMBENTS):
        return json.load(open(INCUMBENTS))
    return {}


def emit(tasks, stage):
    path = os.path.join(BASE, "data", "cs2", f"tasks_{stage}.txt")
    with open(path, "w") as f:
        f.write("\n".join(tasks) + "\n")
    print(f"{len(tasks)} tasks -> {path}")
    print(f"sbatch --array=0-{len(tasks) - 1}%24 slurm/cs2_rungs.sbatch "
          f"{path}")


def scan_rung(lang, mb, ctx=4096, seed=0, size="10m"):
    """Return [(final_val_bpb, lr, epochs, run)] for finished runs."""
    out = []
    for p in glob.glob(os.path.join(BASE, "results_cs", "runs", "*.json")):
        r = json.load(open(p))
        if (r.get("lang") == lang and r.get("seed") == seed
                and r.get("ctx") == ctx and r.get("size") == size
                and r.get("doc_reset")
                and abs(r.get("train_bytes", -1) - mb) <= 3):
            ep = r.get("epochs")
            if ep is None:
                continue  # pre-v1 json without the epochs field
            out.append((r["final_val_bpb"], r["lr"], ep, r["run"]))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["hp", "pick", "walk", "ladder", "capacity",
                             "capacity-verdict"])
    ap.add_argument("--frac", type=float, default=None)
    ap.add_argument("--expect-set", choices=["grid", "walk"], default=None,
                    help="pick: the frozen candidate set to require "
                         "EXACTLY (fail-closed on extras/gaps/dups)")
    ap.add_argument("--langs", default=",".join(LANGS))
    args = ap.parse_args()
    langs = args.langs.split(",")

    if args.stage == "hp":
        tasks = []
        for lang in langs:
            b = boundaries(lang)
            mb = b[min(b)]
            for lr in HP_LRS:
                for ep in HP_EPOCHS:
                    tasks.append(f"{lang} {mb} 4096 0 {lr} {ep} "
                                 f"-hp{fkey(min(b))}-lr{lr}-e{ep}")
        emit(tasks, "hp")
        return

    if args.stage == "pick":
        assert args.frac is not None and args.expect_set
        inc = load_incumbents()
        deficit = []
        for lang in langs:
            b = boundaries(lang)
            mb = b[fkey_lookup(b, args.frac)]
            runs = scan_rung(lang, mb)
            # frozen-set exactness (round-5): candidates must be exactly
            # the frozen combo set — no extras, no gaps, no duplicates
            if args.expect_set == "grid":
                want = {(round(lr, 8), ep) for lr in HP_LRS
                        for ep in HP_EPOCHS}
            else:
                prev = FRACS[FRACS.index(args.frac) - 1]
                cur = inc.get(lang, {}).get(fkey(prev))
                if cur is None:
                    deficit.append(f"{lang}:no-prev-incumbent")
                    continue
                lr0, ep0 = cur["lr"], cur["epochs"]
                want = {(round(lr, 8), ep)
                        for lr in (lr0 * 3, lr0, lr0 / 3)
                        for ep in (ep0, ep0 * 2)} | {(round(lr0, 8), ep0)}
            combos = {}
            bad = None
            for val, lr, ep, name in runs:
                key = (round(lr, 8), ep)
                if key not in want:
                    bad = f"extra combo {key}"
                elif key in combos:
                    bad = f"duplicate combo {key}"
                else:
                    combos[key] = (val, lr, ep, name)
            if bad or set(combos) != want:
                deficit.append(f"{lang}:{bad or 'missing combos'} "
                               f"({len(combos)}/{len(want)})")
                continue
            best = min(combos.values())
            inc.setdefault(lang, {})[fkey(args.frac)] = dict(
                lr=best[1], epochs=best[2], val_bpb=best[0], run=best[3],
                n_candidates=len(runs))
            print(f"[{lang}] frac {args.frac}: lr={best[1]} ep={best[2]} "
                  f"val={best[0]:.4f} ({len(runs)} candidates)")
        os.makedirs(os.path.dirname(INCUMBENTS), exist_ok=True)
        json.dump(inc, open(INCUMBENTS, "w"), indent=1)
        if deficit:  # fail-closed (round-2 NB5): drivers must abort
            raise SystemExit(f"pick incomplete at frac {args.frac}: "
                             + " ".join(deficit))
        return

    if args.stage == "walk":
        assert args.frac is not None
        inc = load_incumbents()
        prev = FRACS[FRACS.index(args.frac) - 1]
        tasks = []
        for lang in langs:
            b = boundaries(lang)
            mb = b[fkey_lookup(b, args.frac)]
            cur = inc.get(lang, {}).get(fkey(prev))
            if cur is None:
                print(f"[{lang}] no incumbent at prev frac {prev}; skip")
                continue
            lr0, ep0 = cur["lr"], cur["epochs"]
            combos = {(lr0, ep0)}
            for lr in (lr0 * 3, lr0, lr0 / 3):
                for ep in (ep0, ep0 * 2):
                    combos.add((round(lr, 8), ep))
            for lr, ep in sorted(combos):
                tasks.append(f"{lang} {mb} 4096 0 {lr} {ep} "
                             f"-w{fkey(args.frac)}-lr{lr}-e{ep}")
        emit(tasks, f"walk_{fkey(args.frac)}")
        return

    if args.stage == "ladder":
        inc = load_incumbents()
        tasks = []
        for lang in langs:
            b = boundaries(lang)
            for f in FRACS:
                cur = inc.get(lang, {}).get(fkey(f))
                if cur is None:  # fail-closed (round-2 NB5)
                    raise SystemExit(
                        f"ladder refused: missing incumbent {lang}@{f}")
                mb = b[fkey_lookup(b, f)]
                for ctx in (512, 4096):
                    for s in (0, 1, 2):
                        tasks.append(
                            f"{lang} {mb} {ctx} {s} {cur['lr']} "
                            f"{cur['epochs']} -r{fkey(f)}-c{ctx}")
        emit(tasks, "ladder")
        return

    if args.stage == "capacity":
        # tuned 30m probe = the full 6-run neighbor set around the 10m
        # incumbent (ARM_CS §4, round-2 NB5 fix)
        inc = load_incumbents()
        tasks = []
        for lang in langs:
            b = boundaries(lang)
            f = FRACS[-1]
            cur = inc.get(lang, {}).get(fkey(f))
            if cur is None:
                raise SystemExit(f"capacity refused: no incumbent {lang}")
            lr0, ep0 = cur["lr"], cur["epochs"]
            combos = {(lr0, ep0)}
            for lr in (lr0 * 3, lr0, lr0 / 3):
                for ep in (ep0, ep0 * 2):
                    combos.add((round(lr, 8), ep))
            for lr, ep in sorted(combos):
                tasks.append(f"{lang} {b[fkey_lookup(b, f)]} 4096 0 "
                             f"{lr} {ep} -cap30m-lr{lr}-e{ep}")
        emit(tasks, "capacity")
        print("submit with: sbatch --export=ALL,CS2_SIZE=30m "
              "--array=... slurm/cs2_rungs.sbatch data/cs2/"
              "tasks_capacity.txt")
        return

    if args.stage == "capacity-verdict":
        # adjudication artifact (ARM_CS §4): best tuned 30m probe vs the
        # tuned 10m incumbent; envelope withholds H3 without this file
        inc = load_incumbents()
        verdict = {}
        for lang in langs:
            b = boundaries(lang)
            f = FRACS[-1]
            mb = b[fkey_lookup(b, f)]
            cur = inc.get(lang, {}).get(fkey(f))
            if cur is None:
                raise SystemExit(f"verdict refused: no incumbent {lang}")
            probes = scan_rung(lang, mb, size="30m")
            lr0, ep0 = cur["lr"], cur["epochs"]
            want = {(round(lr, 8), ep) for lr in (lr0 * 3, lr0, lr0 / 3)
                    for ep in (ep0, ep0 * 2)} | {(round(lr0, 8), ep0)}
            per_combo = {}
            for val, lr, ep, name in probes:
                key = (round(lr, 8), ep)
                if key in want:
                    per_combo.setdefault(key, []).append((val, name))
            dup = [k for k, v in per_combo.items() if len(v) > 1]
            if dup or set(per_combo) != want:
                raise SystemExit(
                    f"verdict refused: {lang} 30m probes must be exactly "
                    f"the frozen set (missing {sorted(want - set(per_combo))},"
                    f" duplicates {dup})")
            best30, best30_run = min(v[0] for v in per_combo.values())
            best10 = cur["val_bpb"]
            run_shas = {name: _result_sha(name)
                        for v in per_combo.values() for _, name in v}
            verdict[lang] = dict(
                schema="cs_capacity_verdict_v1",
                fired=bool(best30 < best10 - 0.01),
                best_30m=best30, best_30m_run=best30_run,
                best_10m=best10, incumbent_run=cur.get("run"),
                probe_run_sha256=run_shas)
            print(f"[{lang}] 30m {best30:.4f} vs 10m {best10:.4f} "
                  f"fired={verdict[lang]['fired']}")
        out = os.path.join(BASE, "results_cs", "capacity_verdict.json")
        if os.path.exists(out):
            raise SystemExit(f"refusing to overwrite {out} (delete it "
                             "deliberately if re-adjudicating)")
        json.dump(verdict, open(out, "w"), indent=1)
        print(f"wrote {out}")
        return


def _result_sha(run_name):
    import hashlib
    p = os.path.join(BASE, "results_cs", "runs", run_name + ".json")
    h = hashlib.sha256()
    h.update(open(p, "rb").read())
    return h.hexdigest()


def fkey_lookup(b, frac):
    for k in b:
        if abs(k - frac) < 1e-9:
            return k
    raise KeyError(frac)


if __name__ == "__main__":
    main()

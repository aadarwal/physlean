#!/usr/bin/env python3
"""ARM_CS CS-2 task-list generator (feeds slurm/cs2_rungs.sbatch).

Stage hp    : per-language HP grid at the SMALLEST rung —
              lr in {3e-4, 1e-3, 3e-3} x epochs in {1, 2, 4}, ctx 4096,
              seed 0 (ARM_CS §4).
Stage ladder: full rung ladders with the chosen per-language HP
              (--hp results_cs/hp_choice.json: {lang: {lr, epochs}}),
              T in {512, 4096}; seeds {0,1,2} at rungs <= 1/16 else {0};
              plus an epochs-x2 spot-check run per rung (seed 0, ctx 4096)
              whose tag ends in "-spot" (capacity/undertuning guard).

Prints tasks to data/cs2/tasks_<stage>.txt and the sbatch submit line.
"""
import argparse
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
FRACS = [1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0]
LANGS = ["lean", "python", "cpp", "latex"]
HP_LRS = [3e-4, 1e-3, 3e-3]
HP_EPOCHS = [1, 2, 4]


def boundaries(lang):
    man = json.load(open(os.path.join(BASE, "data", "cs2",
                                      f"{lang}_cs2.json")))
    return {float(k): v for k, v in man["rung_boundaries"].items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["hp", "ladder"], required=True)
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--hp", default=os.path.join(BASE, "results_cs",
                                                 "hp_choice.json"))
    args = ap.parse_args()
    langs = args.langs.split(",")
    tasks = []
    if args.stage == "hp":
        for lang in langs:
            b = boundaries(lang)
            mb = b[min(b)]
            for lr in HP_LRS:
                for ep in HP_EPOCHS:
                    tasks.append(f"{lang} {mb} 4096 0 {lr} {ep} "
                                 f"-hp-lr{lr}-e{ep}")
    else:
        hp = json.load(open(args.hp))
        for lang in langs:
            b = boundaries(lang)
            lr, ep = hp[lang]["lr"], hp[lang]["epochs"]
            for f in FRACS:
                mb = b[f]
                seeds = [0, 1, 2] if f <= 1 / 16 else [0]
                for ctx in (512, 4096):
                    for s in seeds:
                        tasks.append(f"{lang} {mb} {ctx} {s} {lr} {ep} "
                                     f"-r{f:.4f}-c{ctx}")
                tasks.append(f"{lang} {mb} 4096 0 {lr} {ep * 2} "
                             f"-r{f:.4f}-c4096-spot")
    path = os.path.join(BASE, "data", "cs2", f"tasks_{args.stage}.txt")
    with open(path, "w") as f:
        f.write("\n".join(tasks) + "\n")
    print(f"{len(tasks)} tasks -> {path}")
    print(f"sbatch --array=0-{len(tasks) - 1}%32 slurm/cs2_rungs.sbatch "
          f"{path}")


if __name__ == "__main__":
    main()

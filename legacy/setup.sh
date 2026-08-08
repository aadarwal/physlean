#!/bin/bash
[ "${PHYSLEAN_LEGACY:-0}" = "1" ] || { echo "legacy pilot script: set PHYSLEAN_LEGACY=1 (see legacy/README.md)"; exit 1; }
# One-time setup: clone + patch + build llama.cpp (pinned to b6000), clone corpora.
set -eu
BASE=${PHYSLEAN_BASE:-$(cd "$(dirname "$0")" && pwd)}
cd "$BASE"

# llama.cpp pinned to b6000: current master's training path asserts in
# ggml_build_backward_expand; b6000 (Aug 2025) trains correctly on CPU.
if [ ! -d llama.cpp ]; then
  git clone --depth 1 --branch b6000 https://github.com/ggml-org/llama.cpp
  (cd llama.cpp && git apply "$BASE/patches/llamacpp-b6000-lean-scaling.patch")
fi
cmake -S llama.cpp -B llama.cpp/build -DGGML_NATIVE=ON -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build llama.cpp/build --target llama-perplexity llama-finetune -j"$(nproc)"

# corpora: 2x2 grid {physics, math} x {Lean 4, Python}
mkdir -p corpora && cd corpora
for r in leanprover-community/physlib leanprover-community/mathlib4 qutip/qutip sympy/sympy; do
  d=$(basename "$r")
  [ -d "$d" ] || git clone --depth 1 "https://github.com/$r"
done
cd "$BASE"

# data + models
python3 prep_corpora.py
mkdir -p models
M_E=256 M_L=4 M_H=4 M_F=768 python3 gen_model.py models/base_small.gguf 1234
python3 gen_model.py models/base_11m.gguf 1234   # larger variant for GPU runs

echo "setup complete. next: bash run_pilot.sh && python3 analyze.py"

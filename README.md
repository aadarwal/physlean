# physlean — testing Gwern's "Lean Software Scaling Laws" on PhysLean/Physlib

A minimal, fully-reproducible pilot of the experiment proposed in Gwern's essay
[Lean Software Scaling Laws](https://gwern.net/lean-scaling) (June 2026): measure
how the *predictability* of source code scales with the amount of codebase in
context, compared across programming languages — with a formal, machine-checked
language (Lean 4) as the star test case.

As of August 2026 no public implementation of the proposed measurement existed
(no citing papers, no repos, no datasets). This is a first attempt, sized to run
on a 2-core CPU box in ~4 hours.

## Design

Instead of evaluating a pretrained LLM (whose training data contains ~1000x more
Python than Lean, plus verbatim copies of these very repos), the pilot trains
**small byte-level language models from scratch, one per corpus, with matched
data budgets**. That kills the two worst confounds of the pretrained variant —
training-set contamination and unequal language exposure — at the price of
operating far from frontier scale.

A 2x2 corpus grid separates language effects from domain effects:

|            | Lean 4                          | Python  |
|------------|---------------------------------|---------|
| physics    | Physlib (ex-PhysLean) + QuantumInfo | QuTiP   |
| math       | mathlib4 (sample)               | SymPy   |

- ~2.2 MB training text per corpus (QuTiP is the binding constraint), files
  topologically ordered so imports precede importers; every 10th file held out.
- Byte-level vocab (256 byte tokens + eot, no merges): **1 token = 1 byte**, so
  loss in bits-per-byte is directly comparable across languages with no
  tokenizer confound.
- Model: ~3.5M-param llama-arch transformer (E=256, L=4, H=4, F=768), f32,
  ctx 2048, AdamW lr 3e-4 (swept), 2 epochs, trained with llama.cpp's CPU
  training path (pinned to b6000; see `patches/`).
- Measurement: per-position NLL on held-out streams → bits-per-byte as a
  function of in-context bytes c in [1, 2048), fit to
  **BPB(c) = A * c^(-beta) + Linf** with chunk-bootstrap 95% CIs.
  beta = predictability scaling exponent; Linf = irreducible entropy estimate.

Sanity anchors: a random-init model measures PPL ~257 (= vocab size) flat across
positions; tokenization is verified byte-exact.

## Run it

```sh
bash setup.sh      # clone+patch+build llama.cpp b6000, clone corpora, prep data
bash run_pilot.sh  # train 4 models + eval (hours; STATUS in results/status.txt)
python3 analyze.py # fit exponents -> results/results.json
```

Knobs (env vars): `MODEL`, `CTX`, `EPOCHS`, `LR`, `CORPORA`, `THREADS`;
`M_E/M_L/M_H/M_F` for model size in `gen_model.py`.

## What's in `patches/`

`llamacpp-b6000-lean-scaling.patch` against [llama.cpp](https://github.com/ggml-org/llama.cpp) tag `b6000`:

1. `llama-vocab.cpp`: tolerate a missing BPE merges array (needed for the pure
   byte-level vocab; upstream master later added an equivalent exemption).
2. `finetune.cpp`: env-configurable lr / epochs / val-split / dataset stride /
   output path (`FT_LR`, `FT_EPOCHS`, `FT_VAL`, `FT_STRIDE`, `FT_OUT`).
3. `perplexity.cpp`: `PPL_FIRST` to score from position 0, and `PPL_DUMP` to
   write per-position NLL (`chunk,ctx_len,token_id,nll`) — the core measurement.

llama.cpp is pinned to b6000 because master's training path currently asserts
in `ggml_build_backward_expand`.

## Caveats (pilot-sized, by intent)

- 3.5M params / ~2.2 MB per corpus is far from frontier scale; exponents need
  not transfer upward. The context axis stops at 2 KB, far short of
  codebase-sized contexts.
- Single seed, single model size, constant lr, no data shuffling (topo-order
  curriculum), PhyslibAlpha excluded, results at one training snapshot.
- Held-out files come from the same repos (same authors/style); cross-repo
  generalization is a different question.

## Scale-up roadmap

GPU + the same harness: bigger models (`base_11m.gguf` included), 32k+ contexts,
multiple seeds, shuffling ablation, more languages (Rust/C++ physics codes,
Coq/Isabelle), and a pretrained-model anchor (e.g. a small coder model, with
post-cutoff held-out files to control contamination).

## Provenance

Motivated by [gwern.net/lean-scaling](https://gwern.net/lean-scaling).
Corpora: [Physlib](https://github.com/leanprover-community/physlib),
[mathlib4](https://github.com/leanprover-community/mathlib4),
[QuTiP](https://github.com/qutip/qutip), [SymPy](https://github.com/sympy/sympy)
(each under its own license; not vendored here).
Built with [llama.cpp](https://github.com/ggml-org/llama.cpp).

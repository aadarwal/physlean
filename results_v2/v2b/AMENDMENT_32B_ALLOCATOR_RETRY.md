# Prospective amendment: one 32b battery re-attempt under expandable-segments allocation

Date: 2026-08-10 EDT. Status: DRAFT pending review. Boundary: the first
32b battery attempt (job 20035959) is recorded infeasible (fp32 leg OOM,
~540MiB short of a 141GB H200); no 32b score exists; this amendment is
written before any re-attempt runs.

Authorized change, exactly one: the q25c-32b battery and scoring
launches export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True. This
is CUDA allocator POLICY (memory-segment management), not arithmetic:
kernels, dtype, chunk shape, token counts, and every frozen numeric gate
are untouched, and the environment fingerprint (packages, python binary,
torch build) is identical — the same remedy class as moving the 7b
battery to H200. It is applied to the 32b tier only; every other tier's
recipe is byte-unchanged.

Frozen decision rule: if this re-attempt passes plumbing/gate/identity,
FULL_TIER_SET is restored to six by the post-pass commit and Part A's
existing machinery proceeds unchanged (H200-only, one scoring
submission, per-tier tree pin filled post-scoring, ledger v2
prior-carry, reproduction gates). If it OOMs or fails for ANY reason,
q25c-32b infeasibility is FINAL for this campaign — no third attempt
without a fresh reviewed amendment, and no other memory remedy
(sharding, offload, precision change) is authorized by this document.
The first attempt's failed artifact remains diagnostic evidence.

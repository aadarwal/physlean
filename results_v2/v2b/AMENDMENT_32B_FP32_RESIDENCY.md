# Amendment: 32b fp32-oracle residency correction (supersedes the retry finality clause)

Date: 2026-08-10 EDT. Status: **ADOPTED** (independent review: diagnosis sound — identical OOM under expandable_segments = real allocation; offload/restore is pure lifecycle outside MEASUREMENT_HARNESS_FILES with sealed batteries untouched; supersession valid via the finality clause's own escape on an outcome-free basis; closure rule conservative. Operational note adopted into the run plan: host RSS may exceed the default 128G sbatch cap with 65GB offloaded + fp32 CPU materialization, so the re-attempt submits with --mem=256G; a host-side kill counts as the final failure). Boundary: both 32b
battery attempts are recorded failures; no 32b score exists; this
amendment reads no outcome — its basis is CODE INSPECTION of the battery
harness, performed after the second failure and prompted by the user's
challenge that a 32B model cannot plausibly be un-loadable.

## Diagnosis (outcome-independent)

`validity_battery.main()` loads the tier model in bf16 and passes it to
every item. Item A frees its own per-family temporary (`del m2`) but the
OUTER model — 65GB for 32b — remains resident on the GPU throughout the
fp32 semantic leg, where it is never computed with. The two OOMs are
therefore 65GB (dead weight) + ~131GB (fp32 oracle) against a 139.8GB
device; identical failure under expandable_segments confirms real
allocation, not fragmentation. Without the dead weight the oracle needs
~136GB and fits. Every smaller tier masked this defect (14b: 28+56GB).
Both prior "infeasibility" closures rested on the false premise that the
memory demand was irreducible; the retry amendment's finality clause is
SUPERSEDED by this reviewed amendment, exactly as its own text provides
("absent a fresh reviewed amendment").

## Authorized change, exactly one

In `item_A`, immediately before the fp32 leg: `model.to("cpu")` +
`empty_cache()`; immediately after the leg's teardown: `model.to(device)`.
Pure memory lifecycle in the harness's existing hygiene pattern; no
computed value, dtype, kernel, chunk, token count, bound, or gate moves;
the outer model plays no role in the oracle either way. The change is
tier-agnostic code but only the 32b battery ever reruns under it: all
committed batteries remain sealed evidence and are byte-unchanged.

## Decision rule

One 32b battery re-attempt under this fix (expandable_segments stays
active per the prior amendment). Pass: proceed per Part A (six-tier
restore by the post-pass commit, H200 scoring, per-tier tree pin, ledger
v2, reproduction gates). Fail for ANY reason: q25c-32b is closed
permanently, with all three failed batteries as diagnostics, and no
further memory remedy of any kind will be entertained for this campaign.

## Process note, recorded

The residency diagnosis should have followed the FIRST OOM; instead two
closures were recorded on an untested premise. Carried lesson: an
infeasibility verdict about resources requires a resource-lifecycle
audit of the harness before it is frozen.

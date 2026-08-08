#!/usr/bin/env bash
# Download the pretrained base-model ladder, pinned to the HF revisions
# recorded in models.json, and verify each snapshot locally. Fail-closed:
# metadata failure or any model failure exits nonzero.
set -euo pipefail  # expected failures are explicitly guarded; anything unguarded aborts
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"  # models.json paths below are relative
export HF_XET_HIGH_PERFORMANCE=1  # hf-xet backend (current; hf_transfer is deprecated)

SMALLMID=(
  Qwen/Qwen2.5-Coder-0.5B
  Qwen/Qwen2.5-Coder-1.5B
  Qwen/Qwen2.5-Coder-3B
  Qwen/Qwen2.5-Coder-7B
  Qwen/Qwen3-0.6B-Base
  Qwen/Qwen3-1.7B-Base
  Qwen/Qwen3-4B-Base
  Qwen/Qwen3.5-0.8B-Base
  Qwen/Qwen3.5-2B-Base
  Qwen/Qwen3.5-4B-Base
  bigcode/starcoder2-3b
)
# Qwen3-32B-Base does not exist on HF (401 verified); ladder tops at 14B
BIGM=(
  Qwen/Qwen2.5-Coder-14B
  Qwen/Qwen2.5-Coder-32B
  Qwen/Qwen3-8B-Base
  Qwen/Qwen3-14B-Base
  Qwen/Qwen3.5-9B-Base
  deepseek-ai/DeepSeek-Coder-V2-Lite-Base
)
MODELS=("${SMALLMID[@]}" "${BIGM[@]}")

# metadata (release dates = contamination cutoffs; sha = pinned revision).
# Existing pins are NEVER silently advanced: only missing models are added.
# Deliberate re-pinning requires REPIN=1.
"$BASE/.venv/bin/python" - "${MODELS[@]}" <<'EOF' || { echo "METADATA-FAILED"; exit 1; }
import json, os, sys, urllib.request
out = {}
if os.path.exists("models.json") and os.environ.get("REPIN") != "1":
    out = json.load(open("models.json"))
for mid in sys.argv[1:]:
    if mid in out and out[mid].get("sha"):
        print(mid, "pinned", out[mid]["sha"][:12], flush=True)
        continue
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{mid}", timeout=60) as r:
        d = json.load(r)
    assert d.get("sha"), f"no sha for {mid}"
    out[mid] = dict(created=d.get("createdAt"), sha=d["sha"])
    print(mid, d.get("createdAt"), d["sha"][:12], flush=True)
json.dump(out, open("models.json", "w"), indent=1)
EOF

# Staged acquisition (review): the cheapest validity check must not wait
# on 300GB. STAGE=battery (default, ~10GB: the 4 battery-family smalls) |
# smallmid (11-model exploratory scope) | big (H200 rungs) | all.
STAGE="${STAGE:-battery}"
case "$STAGE" in
  battery)  DL=(Qwen/Qwen2.5-Coder-0.5B Qwen/Qwen3-0.6B-Base \
                Qwen/Qwen3.5-0.8B-Base bigcode/starcoder2-3b);;
  smallmid) DL=("${SMALLMID[@]}");;
  big)      DL=("${BIGM[@]}");;
  all)      DL=("${MODELS[@]}");;
  *) echo "unknown STAGE=$STAGE"; exit 2;;
esac
echo "=== stage: $STAGE (${#DL[@]} models; metadata pinned for all) ==="

NFAIL=0
for m in "${DL[@]}"; do
  echo "=== downloading $m ==="
  # python API pinned to the recorded revision, then verified from cache
  "$BASE/.venv/bin/python" - "$m" <<'PYEOF' || { echo "[FAIL] $m"; NFAIL=$((NFAIL+1)); }
import json, sys
from huggingface_hub import snapshot_download
mid = sys.argv[1]
rev = json.load(open("models.json"))[mid]["sha"]
snapshot_download(mid, revision=rev, max_workers=8)
snapshot_download(mid, revision=rev, local_files_only=True)  # verify
print("[ok]", mid, "@", rev[:12], flush=True)
PYEOF
done

if [ "$NFAIL" -ne 0 ]; then
  echo "MODEL DOWNLOADS INCOMPLETE ($NFAIL failed)"
  exit 1
fi
echo "ALL MODEL DOWNLOADS DONE"

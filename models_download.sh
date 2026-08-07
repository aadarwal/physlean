#!/usr/bin/env bash
# Download the pretrained base-model ladder and record per-model HF repo
# creation dates (= conservative contamination cutoffs) into models.json.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
export HF_HUB_ENABLE_HF_TRANSFER=1

MODELS=(
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

"$BASE/.venv/bin/python" - "${MODELS[@]}" <<'EOF'
import json, sys, urllib.request
out = {}
for mid in sys.argv[1:]:
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{mid}", timeout=30) as r:
        d = json.load(r)
    out[mid] = dict(created=d.get("createdAt"), sha=d.get("sha"))
    print(mid, d.get("createdAt"), flush=True)
json.dump(out, open("models.json", "w"), indent=1)
EOF

for m in "${MODELS[@]}"; do
  echo "=== downloading $m ==="
  "$BASE/.venv/bin/hf" download "$m" --quiet || echo "[FAIL] $m"
done
echo "ALL MODEL DOWNLOADS DONE"

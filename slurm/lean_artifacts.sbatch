#!/bin/bash
#SBATCH -J physlean-lean-arts
#SBATCH -o logs/slurm-%x-%j.out
#SBATCH --nodes=1
#SBATCH -c 16
#SBATCH --mem=128G
#SBATCH -t 08:00:00
# Build the compiler artifacts required by the frozen G3.5/V2-a extraction
# gate. Submit from the repository root with:
#   sbatch -p mit_normal slurm/lean_artifacts.sbatch
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

# Engaging HOME is file-quota-starved. Elan, toolchains, build caches, and
# temporary files must all live on the project POOL.
V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"
export ELAN_HOME="$V2_POOL_BASE/elan"
export XDG_CACHE_HOME="$V2_POOL_BASE/xdg-cache"
export TMPDIR="$V2_POOL_BASE/tmp"
export LEAN_NUM_THREADS="${SLURM_CPUS_PER_TASK:-16}"
# Engaging's compute image is glibc 2.28, while the v4.33-rc2 bundled
# Clang dynamically requires glibc 2.29. `leanc` officially honors
# LEAN_CC; use the node compiler for generated C while keeping Lean,
# Lake, corpus revisions, and all elaboration artifacts unchanged.
export LEAN_CC="/bin/cc"
# mathlib downloads a pinned static curl whose compiled-in CA location is
# absent on Engaging's RHEL image. This is the node's managed trust bundle;
# without the override curl reports OpenSSL STORE "unregistered scheme".
export CURL_CA_BUNDLE="/etc/pki/tls/cert.pem"
export PATH="$ELAN_HOME/bin:$PATH"
V2_BASE_LIBRARY_PATH="${LIBRARY_PATH-}"
V2_BOOTSTRAP_DIR="$V2_POOL_BASE/bootstrap/elan-4.2.3"
mkdir -p "$ELAN_HOME" "$XDG_CACHE_HOME" "$TMPDIR" \
  "$V2_BOOTSTRAP_DIR" results_v2/v2a

# Pin both the release and the bytes of the Elan bootstrap. The tagged
# elan-init shell script fetches "latest", so use the versioned asset itself.
V2_ELAN_VERSION="4.2.3"
V2_ELAN_ASSET="elan-x86_64-unknown-linux-gnu.tar.gz"
V2_ELAN_URL="https://github.com/leanprover/elan/releases/download/v${V2_ELAN_VERSION}/${V2_ELAN_ASSET}"
V2_ELAN_SHA256="df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2"
V2_ELAN_ARCHIVE="$V2_BOOTSTRAP_DIR/$V2_ELAN_ASSET"

if [[ ! -x "$ELAN_HOME/bin/elan" ]]; then
  if [[ ! -f "$V2_ELAN_ARCHIVE" ]]; then
    curl --proto '=https' --tlsv1.2 --fail --location \
      --output "$V2_ELAN_ARCHIVE" "$V2_ELAN_URL"
  fi
  printf '%s  %s\n' "$V2_ELAN_SHA256" "$V2_ELAN_ARCHIVE" | sha256sum -c -
  tar -xzf "$V2_ELAN_ARCHIVE" -C "$V2_BOOTSTRAP_DIR"
  "$V2_BOOTSTRAP_DIR/elan-init" -y --no-modify-path \
    --default-toolchain none
fi

V2_ELAN_ACTUAL=$("$ELAN_HOME/bin/elan" --version)
case "$V2_ELAN_ACTUAL" in
  "elan ${V2_ELAN_VERSION}"*) ;;
  *)
    echo "wrong Elan release in $ELAN_HOME: $V2_ELAN_ACTUAL" >&2
    exit 1
    ;;
esac
[[ -x "$LEAN_CC" ]] || {
  echo "system C compiler is unavailable: $LEAN_CC" >&2
  exit 1
}
[[ -r "$CURL_CA_BUNDLE" ]] || {
  echo "system CA bundle is unavailable: $CURL_CA_BUNDLE" >&2
  exit 1
}
V2_CC_ACTUAL=$("$LEAN_CC" --version | head -n 1)
V2_CA_SHA256=$(sha256sum "$CURL_CA_BUNDLE" | cut -d' ' -f1)

V2_JOB_ID="${SLURM_JOB_ID:-manual}"
V2_REPORT="results_v2/v2a/lean_artifacts_job${V2_JOB_ID}.tsv"
V2_REPORT_TMP=$(mktemp "results_v2/v2a/.lean_artifacts_job${V2_JOB_ID}.XXXXXX")
trap 'rm -f "$V2_REPORT_TMP"' EXIT
{
  printf 'key\tvalue\n'
  printf 'status\tbuilding\n'
  printf 'utc_started\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'slurm_job_id\t%s\n' "$V2_JOB_ID"
  printf 'host\t%s\n' "$(hostname -f)"
  printf 'elan\t%s\n' "$V2_ELAN_ACTUAL"
  printf 'elan_asset_sha256\t%s\n' "$V2_ELAN_SHA256"
  printf 'lean_cc\t%s\n' "$LEAN_CC"
  printf 'lean_cc_version\t%s\n' "$V2_CC_ACTUAL"
  printf 'curl_ca_bundle\t%s\n' "$CURL_CA_BUNDLE"
  printf 'curl_ca_bundle_sha256\t%s\n' "$V2_CA_SHA256"
} > "$V2_REPORT_TMP"

v2_require_clean() {
  local V2_CORPUS_DIR=$1
  local V2_DIRT
  V2_DIRT=$(git -C "$V2_CORPUS_DIR" status --porcelain \
    --untracked-files=no)
  if [[ -n "$V2_DIRT" ]]; then
    echo "tracked worktree changes in $V2_CORPUS_DIR:" >&2
    printf '%s\n' "$V2_DIRT" >&2
    return 1
  fi
}

v2_build_corpus() {
  local V2_CORPUS=$1
  local V2_CACHE_MODE=$2
  local V2_EXPECTED_TOOLCHAIN=$3
  local V2_CORPUS_DIR="corpora/$V2_CORPUS"
  local V2_ACTUAL_TOOLCHAIN V2_REPO_SHA V2_SOURCE_COUNT
  local V2_OLEAN_COUNT V2_ILEAN_COUNT V2_LAKE_VERSION
  local V2_LEAN_BIN V2_TOOLCHAIN_ROOT

  [[ -d "$V2_CORPUS_DIR/.git" || -f "$V2_CORPUS_DIR/.git" ]] || {
    echo "missing corpus checkout: $V2_CORPUS_DIR" >&2
    return 1
  }
  V2_ACTUAL_TOOLCHAIN=$(tr -d '\r\n' < "$V2_CORPUS_DIR/lean-toolchain")
  if [[ "$V2_ACTUAL_TOOLCHAIN" != "$V2_EXPECTED_TOOLCHAIN" ]]; then
    echo "$V2_CORPUS toolchain drift: expected $V2_EXPECTED_TOOLCHAIN, got $V2_ACTUAL_TOOLCHAIN" >&2
    return 1
  fi
  v2_require_clean "$V2_CORPUS_DIR"

  if ! "$ELAN_HOME/bin/elan" toolchain list | cut -d' ' -f1 | \
      grep -Fqx "$V2_ACTUAL_TOOLCHAIN"; then
    "$ELAN_HOME/bin/elan" toolchain install "$V2_ACTUAL_TOOLCHAIN"
  fi
  V2_LEAN_BIN=$(ELAN_TOOLCHAIN="$V2_ACTUAL_TOOLCHAIN" \
    "$ELAN_HOME/bin/elan" which lean)
  V2_TOOLCHAIN_ROOT=$(dirname "$(dirname "$V2_LEAN_BIN")")
  [[ -d "$V2_TOOLCHAIN_ROOT/lib" ]] || {
    echo "$V2_CORPUS toolchain has no library directory: $V2_TOOLCHAIN_ROOT" >&2
    return 1
  }
  # LEAN_CC removes bundled Clang's implicit root/lib search path. GCC's
  # LIBRARY_PATH restores exactly that shipped, version-matched library set
  # (libc++, libc++abi, gmp, uv) without executing the incompatible Clang.
  export LIBRARY_PATH="$V2_TOOLCHAIN_ROOT/lib${V2_BASE_LIBRARY_PATH:+:$V2_BASE_LIBRARY_PATH}"
  V2_LAKE_VERSION=$(cd "$V2_CORPUS_DIR" && \
    "$ELAN_HOME/bin/lake" --version)
  printf '%s: %s; %s\n' "$V2_CORPUS" "$V2_ACTUAL_TOOLCHAIN" \
    "$V2_LAKE_VERSION"

  if [[ "$V2_CACHE_MODE" == "cache" ]]; then
    (cd "$V2_CORPUS_DIR" && "$ELAN_HOME/bin/lake" exe cache get)
  elif [[ "$V2_CACHE_MODE" != "no-cache" ]]; then
    echo "unknown cache mode for $V2_CORPUS: $V2_CACHE_MODE" >&2
    return 1
  fi
  (cd "$V2_CORPUS_DIR" && "$ELAN_HOME/bin/lake" build)
  v2_require_clean "$V2_CORPUS_DIR"

  V2_REPO_SHA=$(git -C "$V2_CORPUS_DIR" rev-parse HEAD)
  V2_SOURCE_COUNT=$(find "$V2_CORPUS_DIR" \
    -path '*/.lake/*' -prune -o -type f -name '*.lean' -print | wc -l)
  V2_OLEAN_COUNT=$(find "$V2_CORPUS_DIR" -type f -name '*.olean' -print | wc -l)
  V2_ILEAN_COUNT=$(find "$V2_CORPUS_DIR" -type f -name '*.ilean' -print | wc -l)
  if (( V2_OLEAN_COUNT == 0 || V2_ILEAN_COUNT == 0 )); then
    echo "$V2_CORPUS build produced no .olean or .ilean artifacts" >&2
    return 1
  fi

  {
    printf '%s.repo_sha\t%s\n' "$V2_CORPUS" "$V2_REPO_SHA"
    printf '%s.toolchain\t%s\n' "$V2_CORPUS" "$V2_ACTUAL_TOOLCHAIN"
    printf '%s.toolchain_root\t%s\n' "$V2_CORPUS" "$V2_TOOLCHAIN_ROOT"
    printf '%s.lake\t%s\n' "$V2_CORPUS" "$V2_LAKE_VERSION"
    printf '%s.source_lean_count\t%s\n' "$V2_CORPUS" "$V2_SOURCE_COUNT"
    printf '%s.olean_count\t%s\n' "$V2_CORPUS" "$V2_OLEAN_COUNT"
    printf '%s.ilean_count\t%s\n' "$V2_CORPUS" "$V2_ILEAN_COUNT"
  } >> "$V2_REPORT_TMP"
}

# Cache-backed builds are safe because both the corpus revisions and
# lean-toolchain files are already pinned; `lake update` is forbidden here.
v2_build_corpus mathlib4 cache leanprover/lean4:v4.33.0-rc2
v2_build_corpus batteries no-cache leanprover/lean4:v4.33.0-rc2
v2_build_corpus physlib cache leanprover/lean4:v4.32.0

{
  printf 'utc_finished\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'status\tcomplete\n'
} >> "$V2_REPORT_TMP"
mv "$V2_REPORT_TMP" "$V2_REPORT"
trap - EXIT
echo "LEAN-ARTIFACTS-DONE $V2_REPORT"

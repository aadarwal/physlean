#!/usr/bin/env python3
"""Static safety contract for the pool-only V2-a Lean artifact build."""
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "slurm", "lean_artifacts.sbatch")


def source():
    return open(SCRIPT, encoding="utf-8").read()


def test_resource_request_fits_engaging_cpu_partition():
    src = source()
    assert "#SBATCH -c 16" in src
    assert "#SBATCH --mem=128G" in src
    assert "#SBATCH -t 08:00:00" in src
    assert "#SBATCH --gres" not in src
    assert "sbatch -p mit_normal" in src


def test_every_cache_and_toolchain_is_pool_only():
    src = source()
    assert 'V2_POOL_BASE="/orcd/pool/008/${USER:?USER is required}"' in src
    for assignment in (
        'export ELAN_HOME="$V2_POOL_BASE/elan"',
        'export XDG_CACHE_HOME="$V2_POOL_BASE/xdg-cache"',
        'export TMPDIR="$V2_POOL_BASE/tmp"',
    ):
        assert assignment in src
    assert not re.search(r"(?m)^\s*(?:export\s+)?HOME=", src)
    assert "~/." not in src


def test_old_glibc_nodes_use_the_system_c_compiler():
    src = source()
    assert 'export LEAN_CC="/bin/cc"' in src
    assert '[[ -x "$LEAN_CC" ]]' in src
    assert "lean_cc_version" in src
    assert 'V2_BASE_LIBRARY_PATH="${LIBRARY_PATH-}"' in src
    assert 'ELAN_TOOLCHAIN="$V2_ACTUAL_TOOLCHAIN"' in src
    assert '"$ELAN_HOME/bin/elan" which lean' in src
    assert 'export LIBRARY_PATH="$V2_TOOLCHAIN_ROOT/lib' in src
    # The compiler override must not alter or update a pinned Lean toolchain.
    assert '"$ELAN_HOME/bin/elan" toolchain install' in src


def test_static_mathlib_curl_uses_engaging_ca_bundle():
    src = source()
    assert 'export CURL_CA_BUNDLE="/etc/pki/tls/cert.pem"' in src
    assert '[[ -r "$CURL_CA_BUNDLE" ]]' in src
    assert 'sha256sum "$CURL_CA_BUNDLE"' in src
    assert "curl_ca_bundle_sha256" in src


def test_toolchain_install_is_resume_safe():
    src = source()
    assert '"$ELAN_HOME/bin/elan" toolchain list' in src
    assert 'grep -Fqx "$V2_ACTUAL_TOOLCHAIN"' in src
    install = src.index('"$ELAN_HOME/bin/elan" toolchain install')
    guard = src.rindex("if !", 0, install)
    assert guard < install


def test_elan_bootstrap_bytes_are_frozen_and_verified():
    src = source()
    assert 'V2_ELAN_VERSION="4.2.3"' in src
    assert (
        'V2_ELAN_SHA256="df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2"'
        in src
    )
    assert "releases/download/v${V2_ELAN_VERSION}" in src
    assert "sha256sum -c -" in src
    assert "--no-modify-path" in src
    assert "--default-toolchain none" in src


def test_all_frozen_corpora_build_and_require_ileans():
    src = source()
    assert (
        "v2_build_corpus mathlib4 cache leanprover/lean4:v4.33.0-rc2"
        in src
    )
    assert (
        "v2_build_corpus batteries no-cache leanprover/lean4:v4.33.0-rc2"
        in src
    )
    assert "v2_build_corpus physlib cache leanprover/lean4:v4.32.0" in src
    assert '"$ELAN_HOME/bin/lake" build' in src
    assert "-name '*.olean'" in src and "-name '*.ilean'" in src
    assert "V2_ILEAN_COUNT == 0" in src
    assert "v2_require_clean" in src
    assert "lake update" not in src.replace("`lake update` is forbidden", "")


def test_success_evidence_is_atomic_and_job_scoped():
    src = source()
    assert 'V2_REPORT="results_v2/v2a/lean_artifacts_job${V2_JOB_ID}.tsv"' in src
    assert "V2_REPORT_TMP=$(mktemp" in src
    assert 'mv "$V2_REPORT_TMP" "$V2_REPORT"' in src
    assert "LEAN-ARTIFACTS-DONE" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"[ok] {name}")
    print("LEAN-ARTIFACT TESTS PASS")

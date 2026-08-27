#!/usr/bin/env python3
"""ARM_CS CS-1 instrument: model-free corpus statistics per language/repo.

Implements ARM_CS.md §2–§3 (draft v0):
  - lag-n byte–byte covariance C(n) (256×256) from WITHIN-DOCUMENT pairs
    only; operator norm, Frobenius norm, top-10 singular values per lag;
  - noise floors: within-document byte-shuffle surrogate (seed 4242) and
    the analytic sqrt(ln V / N_pairs) bound; a lag is VALID iff
    ||C(n)||_op >= 3x max(floors);
  - beta_corr: OLS on (log n, log||C||_op) over the maximal contiguous
    valid prefix; lag-point bootstrap (1000) and document-block bootstrap
    (200, fixed block resamples shared across lags so each resample is a
    coherent curve); two-segment broken-power-law scan (adopt iff
    dBIC <= -6); periodicity peaks (residual > 2x scaled MAD);
  - chain-rule k-gram conditional entropies H(next|k-1 ctx), k <= 6,
    plug-in + Miller–Madow correction, within-document k-grams only;
  - matched-P sensitivity (seed 13 whole-doc subsample to the smallest
    language total) and per-repo strata (repos >= 5 MB).

Collection mirrors prep_pools.py exactly (imports its POOLS/EXCLUDE_DIRS so
the two cannot drift) but takes ALL files (no caps/stride) and keeps repo
labels + document boundaries.

NEW STANDALONE FILE (ARM_CS discipline): touches no frozen instrument, no
results_v2/ artifact, and no dependency beyond numpy (in the cluster lock).

Outputs: results_cs/lang_stats.json + lang_stats_summary.csv. Deterministic
given the working tree (fixed seeds; git commit recorded).
"""
import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np

from prep_pools import EXCLUDE_DIRS, POOLS

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "corpora")
V = 256
LN2 = math.log(2)
SEED_BLOCKS = 7
SEED_SHUFFLE = 4242
SEED_MATCHED = 13
N_BLOCKS = 50
N_DOC_BOOT = 200
N_LAG_BOOT = 1000
FLOOR_MULT = 3.0
MIN_FIT_POINTS = 6
REPO_STRATUM_MIN = 5_000_000
DBIC_ADOPT = -6.0
PEAK_MULT = 2.0
MAX_K = 6
CHUNK = 1 << 25


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def lag_set(max_lag):
    dense = list(range(1, 33))
    sparse = np.unique(np.round(np.logspace(np.log10(33), np.log10(max_lag),
                                            20)).astype(int))
    return sorted(set(dense) | {int(v) for v in sparse if v <= max_lag})


def collect_labeled(lang):
    """prep_pools.collect(), but keeping (repo, blob) and insertion order."""
    out, seen = [], set()
    for repo, dirs, exts in POOLS[lang]:
        for d in dirs:
            top = os.path.join(ROOT, repo, d)
            for dirpath, dirnames, names in os.walk(top):
                dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
                for n in sorted(names):
                    if not any(n.endswith(e) for e in exts):
                        continue
                    p = os.path.join(dirpath, n)
                    try:
                        b = open(p, "rb").read()
                        b.decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    if len(b) < 64:
                        continue
                    h = hashlib.sha1(b).digest()
                    if h in seen:
                        continue
                    seen.add(h)
                    out.append((repo, b if b.endswith(b"\n") else b + b"\n"))
    return out


def build_stream(docs):
    """docs: list[bytes] -> (x uint8, doc_id int32, doc_lens int64)."""
    lens = np.array([len(b) for b in docs], dtype=np.int64)
    x = np.frombuffer(b"".join(docs), dtype=np.uint8)
    doc_id = np.repeat(np.arange(len(docs), dtype=np.int32), lens)
    return x, doc_id, lens


def within_doc_shuffle(x, doc_lens, seed):
    rng = np.random.default_rng(seed)
    out = x.copy()
    pos = 0
    for L in doc_lens:
        seg = out[pos:pos + L]
        rng.shuffle(seg)
        pos += L
    return out


def lag_joint_blocks(x, doc_id, doc_block, n, n_blocks):
    """Per-block joint counts J[b] (256x256 int64) for lag n, within-doc."""
    J = np.zeros(n_blocks * V * V, dtype=np.int64)
    N = x.shape[0]
    if n >= N:
        return J.reshape(n_blocks, V, V), 0
    total = 0
    for a in range(0, N - n, CHUNK):
        b = min(a + CHUNK, N - n)
        left_doc = doc_id[a:b]
        valid = left_doc == doc_id[a + n:b + n]
        if not valid.any():
            continue
        li = x[a:b][valid].astype(np.int64)
        ri = x[a + n:b + n][valid].astype(np.int64)
        blk = doc_block[left_doc[valid]].astype(np.int64)
        key = (blk * V + li) * V + ri
        J += np.bincount(key, minlength=n_blocks * V * V)
        total += int(valid.sum())
    return J.reshape(n_blocks, V, V), total


def cov_norms(J):
    """J: (256,256) counts -> op norm, Frobenius, top-10 singvals of C."""
    N = J.sum()
    if N < 1000:
        return None
    Jn = J.astype(np.float64) / N
    p = Jn.sum(axis=1)
    q = Jn.sum(axis=0)
    C = Jn - np.outer(p, q)
    sv = np.linalg.svd(C, compute_uv=False)
    return dict(op=float(sv[0]), fro=float(np.sqrt((C * C).sum())),
                top10=[float(v) for v in sv[:10]], n_pairs=int(N),
                s2max=float(p.max() * q.max()))


def ols_loglog(lags, vals):
    lx = np.log(np.asarray(lags, dtype=float))
    ly = np.log(np.asarray(vals, dtype=float))
    A = np.vstack([lx, np.ones_like(lx)]).T
    coef, res, _, _ = np.linalg.lstsq(A, ly, rcond=None)
    pred = A @ coef
    sse = float(((ly - pred) ** 2).sum())
    sst = float(((ly - ly.mean()) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    return float(-coef[0]), float(coef[1]), sse, r2, (ly - pred)


def fit_beta(lags, ops, valid):
    """Fits over the maximal contiguous valid prefix. Returns dict or None."""
    # all valid lags before the first run of >=3 consecutive invalid lags
    # (isolated dips are structure, not the death of the signal)
    idx, run = [], 0
    for i, v in enumerate(valid):
        if v:
            idx.append(i)
            run = 0
        else:
            run += 1
            if run >= 3:
                break
    if len(idx) < MIN_FIT_POINTS:
        return None
    fl = [lags[i] for i in idx]
    fo = [ops[i] for i in idx]
    beta, intercept, sse, r2, resid = ols_loglog(fl, fo)
    m = len(fl)
    rng = np.random.default_rng(11)
    boots = []
    for _ in range(N_LAG_BOOT):
        sel = rng.integers(0, m, m)
        if len(set(fl[i] for i in sel)) < 3:
            continue
        b, _, _, _, _ = ols_loglog([fl[i] for i in sel], [fo[i] for i in sel])
        boots.append(b)
    ci = ([float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]
          if len(boots) > 50 else None)
    # broken power law: independent two-segment fit, BIC comparison
    bic1 = m * math.log(max(sse, 1e-300) / m) + 3 * math.log(m)
    best = None
    for k in range(3, m - 3):
        _, _, s1, _, _ = ols_loglog(fl[:k + 1], fo[:k + 1])
        _, _, s2, _, _ = ols_loglog(fl[k:], fo[k:])
        s = s1 + s2
        if best is None or s < best[1]:
            best = (k, s)
    broken = None
    if best is not None:
        k, s = best
        bic2 = m * math.log(max(s, 1e-300) / m) + 5 * math.log(m)
        b1, _, _, _, _ = ols_loglog(fl[:k + 1], fo[:k + 1])
        b2, _, _, _, _ = ols_loglog(fl[k:], fo[k:])
        broken = dict(adopted=bool(bic2 - bic1 <= DBIC_ADOPT),
                      dbic=float(bic2 - bic1), n_break=int(fl[k]),
                      beta_corr_short=float(b1), beta_corr_long=float(b2))
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    peaks = [int(fl[i]) for i in range(m)
             if mad > 0 and resid[i] > PEAK_MULT * mad]
    return dict(beta_corr=beta, r2=r2, fit_range=[int(fl[0]), int(fl[-1])],
                n_points=m, ci_lag_boot=ci, broken=broken, peak_lags=peaks,
                intercept=float(intercept), idx=idx)


def ngram_entropies(x, doc_id, max_k=MAX_K):
    """Chain-rule conditional entropies H(next | k-1 bytes), bits/byte."""
    out = []
    prev_H = 0.0
    packed = x.astype(np.int64)
    for k in range(1, max_k + 1):
        if k > 1:
            packed = packed[:-1] * V + x[k - 1:].astype(np.int64)
        if k == 1:
            arr = packed
        else:
            valid = doc_id[:-(k - 1)] == doc_id[k - 1:]
            arr = packed[valid]
        N = arr.shape[0]
        if N < 1000:
            break
        _, counts = np.unique(arr, return_counts=True)
        m_support = counts.shape[0]
        H = math.log2(N) - float((counts * np.log2(counts)).sum()) / N
        mm = (m_support - 1) / (2.0 * N * LN2)
        out.append(dict(k=k, H_joint_bits=H, mm_correction=mm,
                        distinct=int(m_support), n=int(N),
                        H_cond=H - prev_H,
                        H_cond_mm=(H + mm) - prev_H,
                        unreliable=bool(mm > 0.02)))
        prev_H = H
    return out


def analyze_stream(docs, lags, n_blocks, doc_boot, surrogate, tag):
    """Full per-scope pipeline. docs: list[bytes]."""
    x, doc_id, doc_lens = build_stream(docs)
    n_docs = len(docs)
    rng = np.random.default_rng(SEED_BLOCKS)
    doc_block = rng.integers(0, n_blocks, n_docs).astype(np.int32)
    boot_sel = (np.random.default_rng(17)
                .integers(0, n_blocks, (N_DOC_BOOT, n_blocks))
                if doc_boot else None)
    xs = within_doc_shuffle(x, doc_lens, SEED_SHUFFLE) if surrogate else None

    ops, fros, top10s, npairs, floors_sh, floors_an = [], [], [], [], [], []
    boot_ops = [] if doc_boot else None
    kept_lags = []
    for n in lags:
        J, total = lag_joint_blocks(x, doc_id, doc_block, n, n_blocks)
        stats = cov_norms(J.sum(axis=0))
        if stats is None:
            log(f"  [{tag}] lag {n}: <1000 pairs, stopping lag scan")
            break
        kept_lags.append(n)
        ops.append(stats["op"])
        fros.append(stats["fro"])
        top10s.append(stats["top10"])
        npairs.append(stats["n_pairs"])
        # random-matrix op-norm floor: (2 sqrt(V)) * max-entry-std
        floors_an.append(2.0 * math.sqrt(V)
                         * math.sqrt(stats["s2max"] / stats["n_pairs"]))
        if surrogate:
            Js, _ = lag_joint_blocks(xs, doc_id, doc_block, n, n_blocks)
            s = cov_norms(Js.sum(axis=0))
            floors_sh.append(s["op"] if s else float("nan"))
        if doc_boot:
            row = []
            for r in range(N_DOC_BOOT):
                Jb = J[boot_sel[r]].sum(axis=0)
                sb = cov_norms(Jb)
                row.append(sb["op"] if sb else float("nan"))
            boot_ops.append(row)
        log(f"  [{tag}] lag {n}: op={stats['op']:.3e} "
            f"pairs={stats['n_pairs']:.2e}")

    # the empirical shuffle surrogate IS the null (same marginals, doc
    # structure, masking, estimator); the analytic bound is reported only
    floor = list(floors_sh) if surrogate else list(floors_an)
    valid = [o >= FLOOR_MULT * f for o, f in zip(ops, floor)]
    fit = fit_beta(kept_lags, ops, valid)
    beta_doc_ci = None
    if doc_boot and fit is not None and boot_ops:
        B = np.array(boot_ops)  # (n_lags, N_DOC_BOOT)
        bs = []
        fit_idx = fit["idx"]
        fit_lags = [kept_lags[i] for i in fit_idx]
        for r in range(B.shape[1]):
            col = B[fit_idx, r]
            if np.isnan(col).any() or (col <= 0).any():
                continue
            b, _, _, _, _ = ols_loglog(fit_lags, col)
            bs.append(b)
        if len(bs) > 50:
            beta_doc_ci = [float(np.percentile(bs, 2.5)),
                           float(np.percentile(bs, 97.5))]
    if fit is not None:
        fit["ci_doc_boot"] = beta_doc_ci
    ngrams = ngram_entropies(x, doc_id)
    return dict(lags=kept_lags, op=ops, fro=fros, top10=top10s,
                n_pairs=npairs,
                floor_shuffle=floors_sh if surrogate else None,
                floor_analytic=floors_an, valid=[bool(v) for v in valid],
                fit=fit, ngram=ngrams,
                n_docs=n_docs, total_bytes=int(doc_lens.sum()))


def matched_subsample(docs, target_bytes):
    rng = np.random.default_rng(SEED_MATCHED)
    order = rng.permutation(len(docs))
    keep, s = [], 0
    for i in order:
        if s >= target_bytes:
            break
        keep.append(i)
        s += len(docs[i][1])
    keep.sort()
    return [docs[i] for i in keep]


def selftest(args):
    rng = np.random.default_rng(0)
    lags = lag_set(256)
    # (a) iid uniform bytes: everything should sit at the floor
    docs = [rng.integers(0, 256, 300_000, dtype=np.uint8).tobytes()
            for _ in range(20)]
    r = analyze_stream([bytes(d) for d in docs], lags, 20, False, True, "iid")
    n_valid = sum(r["valid"])
    assert n_valid <= 2, f"iid: {n_valid} lags above floor (expect ~0)"
    rel = [g["H_cond"] for g in r["ngram"] if not g["unreliable"]]
    assert rel and all(abs(h - 8.0) < 0.05 for h in rel), \
        f"iid reliable H_cond {rel} != 8"
    assert any(g["unreliable"] for g in r["ngram"]), \
        "iid: undersampled high-k not flagged unreliable"
    # (b) sticky Markov chain: exponential correlation decay, op decreasing
    n = 2_000_000
    stay = rng.random(n) < 0.9
    jumps = rng.integers(0, 256, n, dtype=np.uint8)
    xs = np.empty(n, dtype=np.uint8)
    xs[0] = jumps[0]
    for i in range(1, n):  # slow but fine for a selftest
        xs[i] = xs[i - 1] if stay[i] else jumps[i]
    r = analyze_stream([xs.tobytes()], lag_set(64), 10, False, True, "markov")
    v = [o for o, ok in zip(r["op"], r["valid"]) if ok]
    assert len(v) >= 5 and v[0] > v[-1], "markov: op not decreasing"
    assert r["ngram"][1]["H_cond"] < 2.0, "markov: H(next|1) not low"
    # (c) noisy period-10 signal: peak detection near lag 10
    base = np.tile(np.arange(10, dtype=np.uint8) * 20, 200_000 // 10)
    noise_mask = rng.random(base.shape[0]) < 0.3
    noisy = np.where(noise_mask, rng.integers(0, 256, base.shape[0]), base
                     ).astype(np.uint8)
    r = analyze_stream([noisy.tobytes()], lag_set(64), 10, False, True, "per")
    pk = (r["fit"] or {}).get("peak_lags", [])
    assert any(p % 10 == 0 for p in pk), f"periodic: no 10-multiple in {pk}"
    print("SELFTEST PASS", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="lean,python,cpp,latex")
    ap.add_argument("--out", default=os.path.join(BASE, "results_cs"))
    ap.add_argument("--max-lag", type=int, default=8192)
    ap.add_argument("--quick", action="store_true",
                    help="smoke: cap 3MB/lang, max-lag 512, no bootstraps")
    ap.add_argument("--no-doc-boot", action="store_true")
    ap.add_argument("--no-matched", action="store_true")
    ap.add_argument("--no-repos", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest(args)
        return

    langs = args.langs.split(",")
    max_lag = 512 if args.quick else args.max_lag
    doc_boot = not (args.quick or args.no_doc_boot)
    lags = lag_set(max_lag)
    os.makedirs(args.out, exist_ok=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE,
                                capture_output=True, text=True
                                ).stdout.strip()
    except OSError:
        commit = None

    collected = {}
    for lang in langs:
        docs = collect_labeled(lang)
        if args.quick:
            capped, s = [], 0
            for d in docs:
                if s >= 3_000_000:
                    break
                capped.append(d)
                s += len(d[1])
            docs = capped
        collected[lang] = docs
        by_repo = {}
        for repo, b in docs:
            by_repo[repo] = by_repo.get(repo, 0) + len(b)
        log(f"[{lang}] {len(docs)} docs, "
            f"{sum(len(b) for _, b in docs)/1e6:.1f}MB, repos={by_repo}")

    result = dict(schema="cs1_lang_stats_v1", commit=commit,
                  constants=dict(seed_blocks=SEED_BLOCKS,
                                 seed_shuffle=SEED_SHUFFLE,
                                 seed_matched=SEED_MATCHED,
                                 n_blocks=N_BLOCKS, n_doc_boot=N_DOC_BOOT,
                                 n_lag_boot=N_LAG_BOOT,
                                 floor_mult=FLOOR_MULT,
                                 dbic_adopt=DBIC_ADOPT, max_lag=max_lag,
                                 quick=bool(args.quick)),
                  scopes={})
    min_bytes = min(sum(len(b) for _, b in collected[l]) for l in langs)
    for lang in langs:
        docs = collected[lang]
        blobs = [b for _, b in docs]
        log(f"=== {lang} pooled ===")
        result["scopes"][lang] = analyze_stream(
            blobs, lags, N_BLOCKS, doc_boot, True, lang)
        result["scopes"][lang]["repos"] = {
            r: sum(len(b) for rr, b in docs if rr == r)
            for r in sorted({rr for rr, _ in docs})}
        if not (args.no_matched or args.quick):
            sub = matched_subsample(docs, min_bytes)
            log(f"=== {lang} matched ({min_bytes/1e6:.1f}MB) ===")
            result["scopes"][f"{lang}__matched"] = analyze_stream(
                [b for _, b in sub], lags, N_BLOCKS, False, True,
                f"{lang}~m")
        if not (args.no_repos or args.quick):
            for repo in sorted({r for r, _ in docs}):
                rb = [b for r, b in docs if r == repo]
                if sum(len(b) for b in rb) < REPO_STRATUM_MIN:
                    continue
                log(f"=== {lang}/{repo} ===")
                result["scopes"][f"{lang}/{repo}"] = analyze_stream(
                    rb, lags, min(20, len(rb)), False, True, repo)

    out_json = os.path.join(args.out, "lang_stats.json")
    with open(out_json, "w") as f:
        json.dump(result, f, indent=1)
    rows = []
    for scope, r in result["scopes"].items():
        fit = r.get("fit") or {}
        broken = fit.get("broken") or {}
        h = {g["k"]: g["H_cond"] for g in r.get("ngram", [])}
        rows.append(dict(
            scope=scope, mb=round(r["total_bytes"] / 1e6, 1),
            n_docs=r["n_docs"],
            beta_corr=round(fit["beta_corr"], 4) if fit else None,
            r2=round(fit["r2"], 4) if fit else None,
            fit_lo=(fit.get("fit_range") or [None, None])[0],
            fit_hi=(fit.get("fit_range") or [None, None])[1],
            ci_lag=fit.get("ci_lag_boot"), ci_doc=fit.get("ci_doc_boot"),
            broken_adopted=broken.get("adopted"),
            n_break=broken.get("n_break"),
            beta_short=round(broken["beta_corr_short"], 4) if broken else None,
            beta_long=round(broken["beta_corr_long"], 4) if broken else None,
            peaks=fit.get("peak_lags"),
            H1=round(h.get(1, float("nan")), 4),
            H2=round(h.get(2, float("nan")), 4),
            H4=round(h.get(4, float("nan")), 4),
            H6=round(h.get(6, float("nan")), 4),
        ))
    out_csv = os.path.join(args.out, "lang_stats_summary.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"[done] {len(result['scopes'])} scopes -> {out_json}")
    for row in rows:
        log("  " + json.dumps(row))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""ARM_CS CS-1 instrument v1: model-free corpus statistics per language/repo.

Implements ARM_CS.md §2–§3 (v1, post-adversarial-review):
  - DOC-INTERIOR lag-n byte covariance C(n): estimand is sequential
    structure net of document identity; the between-document composition
    covariance is computed and reported separately, never folded in.
  - Floors: FIVE within-document permutations (seeds 4242..4246), floor =
    max of the five; a lag is VALID iff op >= 1.5x floor. The analytic
    random-matrix bound 2*sqrt(V)*sqrt(pmax*qmax/N) is diagnostic only.
  - Fit window: [1, n_max] with n_max = last lag before the first run of
    >=3 consecutive INVALID lags; ALL window lags enter the OLS (valid and
    invalid alike — nothing inside the window is dropped on its outcome).
    Adequacy gate: n_max >= 10 and R^2 >= 0.7, else "no reportable
    beta_corr" with a recorded reason.
  - Broken law: continuous hinge a + b1*min(x-x0,0) + b2*max(x-x0,0) over
    gridded knots; BIC penalty counts {a,b1,b2,x0,sigma}; adopt iff
    dBIC <= -6.
  - Peaks: |residual| > 2 * (1.4826 * MAD), sign recorded.
  - H_k: chain-rule conditional entropies with the CONDITIONAL
    Miller–Madow correction (m_k - m_{k-1})/(2 N ln 2); contexts distinct
    = m_{k-1}; unreliable iff |correction| > 0.02 b/B; corrected values
    are exported.
  - Uncertainty: document-BLOCK bootstrap (500 blocks pooled / up to 100
    for strata; resample indices fixed across lags so each resample is a
    coherent curve) on pooled, matched, common-support AND per-repo
    scopes; lag-point bootstrap secondary.
  - Scopes: pooled; per-repo (>=5MB); matched-P over {lean,python,cpp}
    only (latex is a self-budgeted format diagnostic, PREREG §2);
    common-support (docs >= 8192 B — document mixture identical at every
    lag <= 4096).
  - Provenance: git commit + dirty flag + per-scope doc-manifest SHA256 +
    constants recorded; quick mode writes *.quick.json; existing outputs
    are never overwritten without --force.
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
SEED_PERMS = [4242, 4243, 4244, 4245, 4246]
SEED_BLOCKS = 7
SEED_MATCHED = 13
N_BLOCKS = 500
N_DOC_BOOT = 200
N_DOC_BOOT_STRATA = 100
N_LAG_BOOT = 1000
FLOOR_MULT = 1.5
MIN_NMAX = 10
MIN_R2 = 0.7
REPO_STRATUM_MIN = 5_000_000
CSUPPORT_MIN_DOC = 8192
DBIC_ADOPT = -6.0
PEAK_MULT = 2.0
MAX_K = 6
MAX_MM = 0.02
CHUNK = 1 << 25
MATCHED_LANGS = ("lean", "python", "cpp")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def lag_set(max_lag):
    dense = list(range(1, 33))
    sparse = np.unique(np.round(np.logspace(np.log10(33), np.log10(max_lag),
                                            20)).astype(int))
    return sorted(set(dense) | {int(v) for v in sparse if v <= max_lag})


def collect_labeled(lang):
    """prep_pools.collect(), keeping (repo, blob) and insertion order."""
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


def doc_manifest_sha(blobs):
    hs = sorted(hashlib.sha1(b).hexdigest() for b in blobs)
    return hashlib.sha256("".join(hs).encode()).hexdigest()


def build_stream(docs):
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
    """Per-block joint counts (n_blocks,256,256) int64, within-doc pairs."""
    J = np.zeros(n_blocks * V * V, dtype=np.int64)
    N = x.shape[0]
    if n >= N:
        return J.reshape(n_blocks, V, V)
    for a in range(0, N - n, CHUNK):
        b = min(a + CHUNK, N - n)
        left_doc = doc_id[a:b]
        valid = left_doc == doc_id[a + n:b + n]
        if not valid.any():
            continue
        li = x[a:b][valid].astype(np.int64)
        ri = x[a + n:b + n][valid].astype(np.int64)
        blk = doc_block[left_doc[valid]].astype(np.int64)
        J += np.bincount((blk * V + li) * V + ri,
                         minlength=n_blocks * V * V)
    return J.reshape(n_blocks, V, V)


def cov_norms(J):
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


def composition_cov_op(docs):
    """||sum_d w_d (p_d - pbar)(p_d - pbar)^T||_op over doc marginals."""
    D = np.zeros((len(docs), V), dtype=np.float64)
    w = np.zeros(len(docs), dtype=np.float64)
    for i, b in enumerate(docs):
        arr = np.frombuffer(b, dtype=np.uint8)
        D[i] = np.bincount(arr, minlength=V) / len(arr)
        w[i] = len(arr)
    w /= w.sum()
    pbar = w @ D
    Dc = D - pbar
    M = Dc.T @ (Dc * w[:, None])
    return float(np.linalg.svd(M, compute_uv=False)[0])


def ols_loglog(lags, vals):
    lx = np.log(np.asarray(lags, dtype=float))
    ly = np.log(np.asarray(vals, dtype=float))
    A = np.vstack([lx, np.ones_like(lx)]).T
    coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
    pred = A @ coef
    sse = float(((ly - pred) ** 2).sum())
    sst = float(((ly - ly.mean()) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    return float(-coef[0]), float(coef[1]), sse, r2, (ly - pred)


def hinge_fit(lx, ly, knots_idx):
    """Continuous hinge OLS; returns (sse, b_short, b_long, knot_i)."""
    best = None
    for k in knots_idx:
        x0 = lx[k]
        A = np.vstack([np.ones_like(lx),
                       np.minimum(lx - x0, 0.0),
                       np.maximum(lx - x0, 0.0)]).T
        coef, _, _, _ = np.linalg.lstsq(A, ly, rcond=None)
        sse = float(((ly - A @ coef) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, float(-coef[1]), float(-coef[2]), k)
    return best


def window_indices(valid):
    """Window = [0, i_max]: last lag before the first 3-consecutive-invalid
    run. Returns ALL indices in the window (valid and invalid alike)."""
    run, i_max = 0, -1
    for i, v in enumerate(valid):
        if v:
            i_max = i
            run = 0
        else:
            run += 1
            if run >= 3:
                break
    return list(range(i_max + 1)) if i_max >= 0 else []


def fit_beta(lags, ops, valid):
    idx = window_indices(valid)
    if not idx:
        return dict(reason="no valid lags")
    fl = [lags[i] for i in idx]
    fo = [ops[i] for i in idx]
    n_max = fl[-1]
    m = len(fl)
    beta, intercept, sse, r2, resid = ols_loglog(fl, fo)
    # peaks are a gate-INDEPENDENT diagnostic: oscillation-dominated
    # corpora fail the power-law gate precisely when peaks matter most
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    peaks = [dict(lag=int(fl[i]), sign=int(np.sign(resid[i])))
             for i in range(m) if mad > 0 and abs(resid[i]) > PEAK_MULT * mad]
    if n_max < MIN_NMAX or not (r2 >= MIN_R2):
        return dict(reason=f"adequacy gate: n_max={n_max} r2={r2:.3f}",
                    window=[int(fl[0]), int(n_max)], beta_unreported=beta,
                    r2=r2, idx=idx, peaks=peaks)
    rng = np.random.default_rng(11)
    boots = []
    for _ in range(N_LAG_BOOT):
        sel = rng.integers(0, m, m)
        if len({fl[i] for i in sel}) < 3:
            continue
        b, _, _, _, _ = ols_loglog([fl[i] for i in sel],
                                   [fo[i] for i in sel])
        boots.append(b)
    ci = ([float(np.percentile(boots, 2.5)),
           float(np.percentile(boots, 97.5))] if len(boots) > 50 else None)
    lx, ly = np.log(np.array(fl, float)), np.log(np.array(fo, float))
    # BIC params: single {a,b,sigma}=3; hinge {a,b1,b2,x0,sigma}=5
    bic1 = m * math.log(max(sse, 1e-300) / m) + 3 * math.log(m)
    broken = None
    if m >= 8:
        h = hinge_fit(lx, ly, list(range(2, m - 2)))
        if h is not None:
            sse2, b1, b2, k = h
            bic2 = m * math.log(max(sse2, 1e-300) / m) + 5 * math.log(m)
            broken = dict(adopted=bool(bic2 - bic1 <= DBIC_ADOPT),
                          dbic=float(bic2 - bic1), n_break=int(fl[k]),
                          beta_corr_short=b1, beta_corr_long=b2)
    return dict(beta_corr=beta, r2=r2, fit_range=[int(fl[0]), int(n_max)],
                n_points=m, ci_lag_boot=ci, broken=broken, peaks=peaks,
                intercept=float(intercept), idx=idx)


def ngram_entropies(x, doc_id, max_k=MAX_K):
    out = []
    prev_H, prev_m = 0.0, 0
    packed = x.astype(np.int64)
    for k in range(1, max_k + 1):
        if k > 1:
            packed = packed[:-1] * V + x[k - 1:].astype(np.int64)
        arr = packed if k == 1 else packed[doc_id[:-(k - 1)] == doc_id[k - 1:]]
        N = arr.shape[0]
        if N < 1000:
            break
        _, counts = np.unique(arr, return_counts=True)
        m_k = counts.shape[0]
        H = math.log2(N) - float((counts * np.log2(counts)).sum()) / N
        corr = (m_k - prev_m) / (2.0 * N * LN2)  # conditional MM correction
        # reliability keys on JOINT undersampling: in saturation (all
        # k-grams distinct) H_cond collapses to ~0 with a tiny correction
        # difference, so the difference alone cannot flag it
        mm_joint = (m_k - 1) / (2.0 * N * LN2)
        out.append(dict(k=k, H_joint_bits=H, distinct_kgrams=int(m_k),
                        distinct_contexts=int(prev_m), n=int(N),
                        H_cond=H - prev_H, mm_cond_correction=corr,
                        mm_joint=mm_joint,
                        H_cond_mm=(H - prev_H) + corr,
                        unreliable=bool(mm_joint > MAX_MM
                                        or abs(corr) > MAX_MM)))
        prev_H, prev_m = H, m_k
    return out


def analyze_stream(docs, lags, n_blocks, n_boot, tag):
    """docs: list[bytes]. Full v1 pipeline for one scope."""
    x, doc_id, doc_lens = build_stream(docs)
    n_docs = len(docs)
    nb = min(n_blocks, n_docs)
    rng = np.random.default_rng(SEED_BLOCKS)
    doc_block = rng.integers(0, nb, n_docs).astype(np.int32)
    boot_M = None
    if n_boot:
        sel = np.random.default_rng(17).integers(0, nb, (n_boot, nb))
        # resampling blocks = weighting blocks by multiplicity: one matmul
        # per lag instead of n_boot gathers
        boot_M = np.stack([np.bincount(sel[r], minlength=nb)
                           for r in range(n_boot)]).astype(np.float64)
    shufs = [within_doc_shuffle(x, doc_lens, s) for s in SEED_PERMS]

    ops, fros, top10s, npairs = [], [], [], []
    floors, floor_spread, floors_an = [], [], []
    boot_ops = [] if n_boot else None
    kept = []
    for n in lags:
        J = lag_joint_blocks(x, doc_id, doc_block, n, nb)
        st = cov_norms(J.sum(axis=0))
        if st is None:
            log(f"  [{tag}] lag {n}: <1000 pairs, stopping")
            break
        kept.append(n)
        ops.append(st["op"])
        fros.append(st["fro"])
        top10s.append(st["top10"])
        npairs.append(st["n_pairs"])
        floors_an.append(2.0 * math.sqrt(V)
                         * math.sqrt(st["s2max"] / st["n_pairs"]))
        perm_ops = []
        for xs in shufs:
            Js = lag_joint_blocks(xs, doc_id, doc_block, n, nb)
            ss = cov_norms(Js.sum(axis=0))
            perm_ops.append(ss["op"] if ss else float("nan"))
        floors.append(float(np.nanmax(perm_ops)))
        floor_spread.append([float(np.nanmin(perm_ops)),
                             float(np.nanmax(perm_ops))])
        if n_boot:
            R = boot_M @ J.reshape(nb, V * V).astype(np.float64)
            row = []
            for r in range(n_boot):
                sb = cov_norms(R[r].reshape(V, V))
                row.append(sb["op"] if sb else float("nan"))
            boot_ops.append(row)
        log(f"  [{tag}] lag {n}: op={st['op']:.3e} floor={floors[-1]:.3e}")

    valid = [o >= FLOOR_MULT * f for o, f in zip(ops, floors)]
    fit = fit_beta(kept, ops, valid)
    if fit.get("idx"):
        # Frobenius slope over the SAME window: fro is an average statistic
        # robust to single-entry spikes (long constant runs); op-vs-fro
        # slope divergence flags run-length domination of the op norm
        fi = fit["idx"]
        bf, _, _, _, _ = ols_loglog([kept[i] for i in fi],
                                    [fros[i] for i in fi])
        fit["beta_corr_fro"] = bf
    if n_boot and boot_ops and fit.get("idx"):
        B = np.array(boot_ops)
        fit_idx = fit["idx"]
        fit_lags = [kept[i] for i in fit_idx]
        bs = []
        for r in range(B.shape[1]):
            col = B[fit_idx, r]
            if np.isnan(col).any() or (col <= 0).any():
                continue
            b, _, _, _, _ = ols_loglog(fit_lags, col)
            bs.append(b)
        if len(bs) > 30:
            fit["ci_doc_block_boot"] = [float(np.percentile(bs, 2.5)),
                                        float(np.percentile(bs, 97.5))]
            fit["n_blocks"] = int(nb)
    return dict(lags=kept, op=ops, fro=fros, top10=top10s, n_pairs=npairs,
                floor_perm_max=floors, floor_perm_range=floor_spread,
                floor_analytic_diag=floors_an,
                valid=[bool(v) for v in valid], fit=fit,
                composition_cov_op=composition_cov_op(docs),
                ngram=ngram_entropies(x, doc_id),
                n_docs=n_docs, total_bytes=int(doc_lens.sum()),
                doc_manifest_sha256=doc_manifest_sha(docs))


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


def git_identity():
    try:
        c = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE,
                           capture_output=True, text=True).stdout.strip()
        d = subprocess.run(
            ["git", "status", "--porcelain", "--", ".",
             ":(exclude)results_cs", ":(exclude)results_v2"],
            cwd=BASE, capture_output=True, text=True).stdout.strip()
        return c, bool(d)
    except OSError:
        return None, None


def selftest():
    rng = np.random.default_rng(0)
    lags = lag_set(256)
    # (a) iid uniform: nothing above floor; reliable H_cond = 8; high-k flagged
    docs = [rng.integers(0, 256, 300_000, dtype=np.uint8).tobytes()
            for _ in range(20)]
    r = analyze_stream(docs, lags, 20, 0, "iid")
    assert sum(r["valid"]) <= 2, f"iid: {sum(r['valid'])} valid lags"
    rel = [g["H_cond_mm"] for g in r["ngram"] if not g["unreliable"]]
    assert rel and all(abs(h - 8.0) < 0.05 for h in rel), f"iid H {rel}"
    assert any(g["unreliable"] for g in r["ngram"]), "iid: high-k not flagged"
    # (b) sticky Markov: decreasing op, low H(next|prev)
    n = 2_000_000
    stay = rng.random(n) < 0.9
    jumps = rng.integers(0, 256, n, dtype=np.uint8)
    xs = np.empty(n, dtype=np.uint8)
    xs[0] = jumps[0]
    for i in range(1, n):
        xs[i] = xs[i - 1] if stay[i] else jumps[i]
    r = analyze_stream([xs.tobytes()], lag_set(64), 10, 0, "markov")
    v = [o for o, ok in zip(r["op"], r["valid"]) if ok]
    assert len(v) >= 5 and v[0] > v[-1], "markov: op not decreasing"
    assert r["ngram"][1]["H_cond_mm"] < 2.0, "markov: H(next|1) not low"
    # (c) decaying signal with a lag-10 echo: peak at a multiple of 10
    n = 1_500_000
    u = rng.random(n)
    jumps = rng.integers(0, 256, n, dtype=np.uint8)
    xs = np.empty(n, dtype=np.uint8)
    xs[:10] = jumps[:10]
    for i in range(10, n):
        if u[i] < 0.4:
            xs[i] = xs[i - 10]  # periodic echo channel
        elif u[i] < 0.7:
            xs[i] = xs[i - 1]   # decaying Markov channel
        else:
            xs[i] = jumps[i]
    r = analyze_stream([xs.tobytes()], lag_set(64), 10, 0, "echo")
    pk = [p["lag"] for p in (r["fit"] or {}).get("peaks", [])]
    assert any(p % 10 == 0 for p in pk), f"echo: no 10-multiple in {pk}"
    # (d) power-law recovery: Pareto(1.5) renewal segments. Exact theory:
    # rho(n) = (2/3)(1+n)^(-1/2). The OP norm is a max statistic and is
    # dominated by the longest constant runs of a heavy-tailed process
    # (documented estimator property); recovery is asserted on the
    # Frobenius slope over the same frozen window.
    total, segs = 6_000_000, []
    got = 0
    while got < total:
        seg_len = int(min(rng.pareto(1.5) + 1, 2000))
        segs.append(np.full(seg_len, rng.integers(0, 256), dtype=np.uint8))
        got += seg_len
    xs = np.concatenate(segs)[:total]
    docs = [xs[i:i + 300_000].tobytes() for i in range(0, total, 300_000)]
    r = analyze_stream(docs, lag_set(512), 20, 0, "pareto")
    bf = r["fit"].get("beta_corr_fro")
    assert bf is not None and abs(bf - 0.5) < 0.15, \
        f"pareto fro-slope {bf} != 0.5"
    # (e) heterogeneous-doc null: iid WITHIN each doc, two doc classes
    # with disjoint byte supports. Doc-interior C(n) equals the
    # composition covariance here, and so does the within-doc shuffle
    # floor — so NO lag is valid (sequential structure net of doc
    # identity is zero), while the composition covariance itself is
    # large vs the sampling-only analytic bound. This is the estimand
    # declaration of ARM_CS §1 made executable.
    docs = []
    for i in range(50):
        lo, hi = (0, 128) if i % 2 == 0 else (128, 256)
        docs.append(rng.integers(lo, hi, 100_000, dtype=np.int64)
                    .astype(np.uint8).tobytes())
    r = analyze_stream(docs, lag_set(64), 20, 0, "hetero")
    assert sum(r["valid"]) <= 2, \
        f"hetero: doc-interior C above floor ({sum(r['valid'])} lags)"
    an = float(np.mean(r["floor_analytic_diag"]))
    assert r["composition_cov_op"] > max(10 * an, 2e-3), \
        f"hetero: composition {r['composition_cov_op']:.2e} vs analytic {an:.2e}"
    print("SELFTEST PASS", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="lean,python,cpp,latex")
    ap.add_argument("--out", default=os.path.join(BASE, "results_cs"))
    ap.add_argument("--max-lag", type=int, default=8192)
    ap.add_argument("--quick", action="store_true",
                    help="3MB/lang, max-lag 512, no bootstraps, *.quick.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-strata", action="store_true")
    ap.add_argument("--matched-bytes", type=int, default=0,
                    help="0 = auto (min over matched langs present); "
                         "-1 = skip matched scopes; >0 = explicit target")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return

    suffix = ".quick" if args.quick else ""
    out_json = os.path.join(args.out, f"lang_stats{suffix}.json")
    out_csv = os.path.join(args.out, f"lang_stats_summary{suffix}.csv")
    if os.path.exists(out_json) and not args.force:
        sys.exit(f"refusing to overwrite {out_json} (use --force)")

    langs = args.langs.split(",")
    max_lag = 512 if args.quick else args.max_lag
    lags = lag_set(max_lag)
    os.makedirs(args.out, exist_ok=True)
    commit, dirty = git_identity()

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
        log(f"[{lang}] {len(docs)} docs "
            f"{sum(len(b) for _, b in docs)/1e6:.1f}MB "
            f"repos={sorted({r for r, _ in docs})}")

    result = dict(
        schema="cs1_lang_stats_v2", commit=commit, dirty=dirty,
        constants=dict(seed_perms=SEED_PERMS, seed_blocks=SEED_BLOCKS,
                       seed_matched=SEED_MATCHED, n_blocks=N_BLOCKS,
                       n_doc_boot=N_DOC_BOOT, floor_mult=FLOOR_MULT,
                       min_nmax=MIN_NMAX, min_r2=MIN_R2,
                       dbic_adopt=DBIC_ADOPT, peak_mult=PEAK_MULT,
                       max_mm=MAX_MM, csupport_min_doc=CSUPPORT_MIN_DOC,
                       max_lag=max_lag, quick=bool(args.quick)),
        scopes={})

    if args.matched_bytes > 0:
        matched_bytes = args.matched_bytes
    elif args.matched_bytes < 0:
        matched_bytes = 0
    else:
        matched_bytes = min((sum(len(b) for _, b in collected[l])
                             for l in MATCHED_LANGS if l in collected),
                            default=0)
    for lang in langs:
        docs = collected[lang]
        blobs = [b for _, b in docs]
        log(f"=== {lang} pooled ===")
        result["scopes"][lang] = analyze_stream(
            blobs, lags, N_BLOCKS, 0 if args.quick else N_DOC_BOOT, lang)
        result["scopes"][lang]["repos"] = {
            r: sum(len(b) for rr, b in docs if rr == r)
            for r in sorted({rr for rr, _ in docs})}
        if args.quick:
            continue
        cs = [b for b in blobs if len(b) >= CSUPPORT_MIN_DOC]
        if len(cs) > 20:
            log(f"=== {lang} common-support ===")
            result["scopes"][f"{lang}__csupport"] = analyze_stream(
                cs, lags, N_BLOCKS, N_DOC_BOOT_STRATA, f"{lang}~cs")
        if lang in MATCHED_LANGS and matched_bytes:
            sub = matched_subsample(docs, matched_bytes)
            log(f"=== {lang} matched ({matched_bytes/1e6:.1f}MB) ===")
            result["scopes"][f"{lang}__matched"] = analyze_stream(
                [b for _, b in sub], lags, N_BLOCKS, N_DOC_BOOT_STRATA,
                f"{lang}~m")
        if not args.no_strata:
            for repo in sorted({r for r, _ in docs}):
                rb = [b for r, b in docs if r == repo]
                if sum(len(b) for b in rb) < REPO_STRATUM_MIN:
                    continue
                log(f"=== {lang}/{repo} ===")
                result["scopes"][f"{lang}/{repo}"] = analyze_stream(
                    rb, lags, 100, N_DOC_BOOT_STRATA, repo)

    with open(out_json, "w") as f:
        json.dump(result, f, indent=1)
    rows = []
    for scope, r in result["scopes"].items():
        fit = r.get("fit") or {}
        broken = fit.get("broken") or {}
        h = {g["k"]: g["H_cond_mm"] for g in r.get("ngram", [])}
        rows.append(dict(
            scope=scope, mb=round(r["total_bytes"] / 1e6, 1),
            n_docs=r["n_docs"],
            beta_corr=round(fit["beta_corr"], 4)
            if "beta_corr" in fit else None,
            no_report_reason=fit.get("reason"),
            r2=round(fit["r2"], 4) if "r2" in fit else None,
            fit_lo=(fit.get("fit_range") or [None, None])[0],
            fit_hi=(fit.get("fit_range") or [None, None])[1],
            ci_lag=fit.get("ci_lag_boot"),
            ci_doc_block=fit.get("ci_doc_block_boot"),
            broken_adopted=broken.get("adopted"),
            n_break=broken.get("n_break"),
            beta_short=round(broken["beta_corr_short"], 4)
            if broken else None,
            beta_long=round(broken["beta_corr_long"], 4) if broken else None,
            peaks=[p["lag"] for p in fit.get("peaks", [])] or None,
            comp_cov_op=f'{r["composition_cov_op"]:.3e}',
            H1=round(h.get(1, float("nan")), 4),
            H2=round(h.get(2, float("nan")), 4),
            H4=round(h.get(4, float("nan")), 4),
            H6=round(h.get(6, float("nan")), 4),
        ))
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"[done] {len(result['scopes'])} scopes -> {out_json}")
    for row in rows:
        log("  " + json.dumps(row))


if __name__ == "__main__":
    main()

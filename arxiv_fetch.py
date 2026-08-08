#!/usr/bin/env python3
"""Fetch arXiv physics LaTeX sources as the informal-prose corpus.

Two eras (the contamination axis for prose, mirroring git add-dates for code):
  old : submitted 2023-01..2023-06  -> predates every model cutoff (fully
        contaminated arm, comparable to code full-splits)
  new : submitted 2026-05..2026-08  -> postdates every local model's release
        (clean arm)

Categories: quant-ph (QuTiP's domain) + hep-th (PhysLean's domain).
Politeness: one request / 3 s against export.arxiv.org. Stdlib only.
Output: corpora/arxiv/{old,new}/<id>.tex (all .tex members of the e-print,
main file first), corpora/arxiv/manifest.json.
"""
import gzip, io, json, os, re, sys, tarfile, time
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "corpora", "arxiv")
UA = {"User-Agent": "physlean-scaling-experiment/0.1 (aadarwal@mit.edu)"}
ATOM = "{http://www.w3.org/2005/Atom}"

ERAS = {
    "old": ("202301010000", "202306302359"),
    "new": ("202605010000", "202608072359"),
}
CATS = ["quant-ph", "hep-th"]
PER_QUERY = 70  # per (era, cat)


def api_list(cat, lo, hi, n):
    ids = []
    start = 0
    while len(ids) < n:
        url = ("https://export.arxiv.org/api/query?search_query="
               f"cat:{cat}+AND+submittedDate:[{lo}+TO+{hi}]"
               f"&start={start}&max_results={min(100, n - len(ids))}"
               "&sortBy=submittedDate&sortOrder=ascending")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            root = ET.fromstring(r.read())
        entries = root.findall(f"{ATOM}entry")
        if not entries:
            break
        for e in entries:
            raw = e.find(f"{ATOM}id").text  # http://arxiv.org/abs/2301.00123v1
            m = re.search(r"abs/([\w.\-/]+?)(v\d+)?$", raw)
            sub = e.find(f"{ATOM}published").text
            if m:  # version captured, no longer discarded (review fix)
                ids.append((m.group(1), (m.group(2) or "v1"), sub))
        start += len(entries)
        time.sleep(3)
    return ids[:n]


def fetch_source(aid, version=None):
    """version like 'v1' pins the exact revision; None = latest (only for
    initial listing, never for reproducing a locked corpus)."""
    url = f"https://export.arxiv.org/e-print/{aid}{version or ''}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
    if blob[:4] == b"%PDF":
        return None
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    texs = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.endswith(".tex"):
                    texs.append((m.name, tf.extractfile(m).read()))
    except tarfile.TarError:
        if blob[:4] == b"%PDF":
            return None
        texs = [("main.tex", blob)]  # single-file source
    docs = []
    for name, b in texs:
        try:
            t = b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                t = b.decode("latin-1")
            except Exception:
                continue
        docs.append((name, t))
    if not docs:
        return None
    docs.sort(key=lambda nt: (0 if "\\documentclass" in nt[1] else 1, nt[0]))
    text = "\n".join(t for _, t in docs)
    return text if len(text.encode("utf-8")) >= 4096 else None


def scan_disk(out_dir):
    """Era-qualified on-disk scan: canonical files key as 'era/safe';
    ANY nested path is an explicit extra. Keying by (era,safe) — not safe
    alone — catches an expected filename sitting in the WRONG era, which
    a set union over eras cannot see (review fix)."""
    found = set()
    for era in ("old", "new"):
        d = os.path.join(out_dir, era)
        for dp, _, ns in os.walk(d):
            for f in ns:
                if f.endswith(".tex"):
                    rel = os.path.relpath(os.path.join(dp, f), d)
                    found.add(f"{era}/{f[:-4]}" if rel == f
                              else f"NESTED:{era}/{rel}")
    return found


def refetch_from_manifest(pin_path):
    """Reproduce the pinned corpus. After the one-time --repin-versions
    adoption the manifest is VERSION+SHA256-exact (explicit vN fetched,
    content hash pinned); legacy entries without sha256 fall back to byte
    counts (weaker; flagged). Existing files failing validation are
    refetched at the pinned version; still-failing files are recorded.
    Emits checksums.json for preflight (exact per-key hash equality)."""
    import hashlib
    pinned = json.load(open(pin_path))
    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "manifest.json")
    checks, mismatched = {}, []

    def valid(dst, m):
        if not os.path.exists(dst):
            return False
        if m.get("sha256"):  # content hash, not just same-size (review fix)
            return hashlib.sha256(
                open(dst, "rb").read()).hexdigest() == m["sha256"]
        return os.path.getsize(dst) == m.get("bytes")

    for safe, m in pinned.items():
        if m.get("skipped"):
            continue
        era = m["era"]
        os.makedirs(os.path.join(OUT, era), exist_ok=True)
        dst = os.path.join(OUT, era, f"{safe}.tex")
        want = m.get("bytes")
        if os.path.exists(dst) and not valid(dst, m):
            print(f"[revalidate] {m['id']} failed "
                  f"{'sha256' if m.get('sha256') else 'byte'} check; "
                  "refetching", flush=True)
            os.remove(dst)
        if not os.path.exists(dst):
            try:
                # version-exact: pinned version if recorded, else v1 —
                # v1 is the defensible choice for the historical arm
                # (extant at submission, certainly pre-cutoff)
                text = fetch_source(m["id"], m.get("version") or "v1")
            except Exception as ex:
                print(f"[err] {m['id']}: {ex}", flush=True)
                text = None
            time.sleep(3)
            if text is None:
                print(f"[warn] pinned {m['id']} not fetchable", flush=True)
                continue
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[ok] {era}/{m['id']}", flush=True)
        blob = open(dst, "rb").read()
        h = hashlib.sha256(blob).hexdigest()
        ok = (h == m["sha256"]) if m.get("sha256") else (len(blob) == want)
        checks[f"{era}/{safe}"] = dict(
            sha256=h, bytes=len(blob), matches_pin=ok,
            pin_kind="sha256" if m.get("sha256") else "bytes")
        if not ok:
            mismatched.append(m["id"])
    with open(man_path, "w") as f:
        json.dump(pinned, f, indent=1)
    expected = {f"{m['era']}/{k}" for k, m in pinned.items()
                if not m.get("skipped")}
    missing = sorted(expected - set(checks))
    # extra on-disk .tex files would be INGESTED by prep_streams (review):
    # they are as fatal as missing ones; keyed era/safe
    on_disk = scan_disk(OUT)
    extra = sorted(on_disk - expected)
    with open(os.path.join(OUT, "checksums.json"), "w") as f:
        json.dump(dict(files=checks, mismatched=mismatched,
                       missing=missing, extra_on_disk=extra), f, indent=1)
    print(f"[validate] {len(checks)}/{len(expected)} files, "
          f"{len(mismatched)} pin failures, {len(missing)} missing, "
          f"{len(extra)} extra on disk", flush=True)
    if missing or mismatched or extra:  # exact expected set or nothing
        print(f"[validate] FAILED missing={missing[:8]} "
              f"mismatched={mismatched[:8]} extra={extra[:8]}", flush=True)
        sys.exit(1)


def repin_versions(pin_path):
    """ONE-TIME manifest regeneration (pre-results only): fetch every
    non-skipped id at its EXPLICIT pinned version (recorded version or v1),
    replacing latest-at-fetch bytes with version-exact bytes + sha256.
    Emits arxiv_manifest.json.new for review + commit."""
    import hashlib
    pinned = json.load(open(pin_path))
    out, failed = {}, []
    for safe, m in pinned.items():
        if m.get("skipped"):
            out[safe] = m  # predeclared skips are preserved verbatim
            continue
        ver = m.get("version") or "v1"
        text = None
        for attempt in range(3):  # transient failure must NEVER redefine
            try:                  # the sample (review blocker)
                text = fetch_source(m["id"], ver)
                break
            except Exception as ex:
                print(f"[retry {attempt+1}/3] {m['id']}{ver}: {ex}",
                      flush=True)
                time.sleep(10 * (attempt + 1))
        time.sleep(3)
        if text is None:
            failed.append(f"{m['id']}{ver}")
            continue
        b = text.encode("utf-8")
        out[safe] = dict(m, version=ver, bytes=len(b),
                         sha256=hashlib.sha256(b).hexdigest())
        era = m["era"]
        os.makedirs(os.path.join(OUT, era), exist_ok=True)
        with open(os.path.join(OUT, era, f"{safe}.tex"), "w",
                  encoding="utf-8") as f:
            f.write(text)
        print(f"[repin] {m['id']}{ver} {len(b)}B", flush=True)
    if failed:
        print(f"[repin] MIGRATION FAILED — {len(failed)} expected sources "
              f"unfetchable, NO candidate written: {failed}", flush=True)
        sys.exit(1)
    with open(pin_path + ".new", "w") as f:
        json.dump(out, f, indent=1)
    print(f"[repin] wrote {pin_path}.new (all expected sources fetched) — "
          "review + commit to adopt")


def main():
    pin = os.path.join(BASE, "arxiv_manifest.json")
    if "--repin-versions" in sys.argv:
        repin_versions(pin)
        return
    if "--from-manifest" in sys.argv or (
            os.path.exists(pin) and not os.path.exists(
                os.path.join(OUT, "manifest.json"))):
        refetch_from_manifest(sys.argv[sys.argv.index("--from-manifest") + 1]
                              if "--from-manifest" in sys.argv
                              and len(sys.argv) > sys.argv.index("--from-manifest") + 1
                              else pin)
        return
    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {}
    for era, (lo, hi) in ERAS.items():
        os.makedirs(os.path.join(OUT, era), exist_ok=True)
        for cat in CATS:
            print(f"[list] {era} {cat}", flush=True)
            for aid, ver, sub in api_list(cat, lo, hi, PER_QUERY):
                safe = aid.replace("/", "_")
                dst = os.path.join(OUT, era, f"{safe}.tex")
                if os.path.exists(dst) or safe in manifest:
                    continue
                try:
                    text = fetch_source(aid, ver)  # Atom-listed version
                except Exception as ex:
                    print(f"[err] {aid}: {ex}", flush=True)
                    time.sleep(3)
                    continue
                time.sleep(3)
                if text is None:
                    manifest[safe] = dict(id=aid, version=ver, era=era,
                                          cat=cat, submitted=sub,
                                          skipped=True)
                    continue
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(text)
                manifest[safe] = dict(id=aid, version=ver, era=era, cat=cat,
                                      submitted=sub,
                                      bytes=len(text.encode("utf-8")))
                print(f"[ok] {era}/{aid} {manifest[safe]['bytes']}B", flush=True)
                with open(man_path, "w") as f:
                    json.dump(manifest, f, indent=1)
    kept = [m for m in manifest.values() if not m.get("skipped")]
    by = {}
    for m in kept:
        by.setdefault(m["era"], []).append(m["bytes"])
    for era, bs in by.items():
        print(f"{era}: {len(bs)} papers, {sum(bs)/1e6:.1f} MB")


if __name__ == "__main__":
    main()

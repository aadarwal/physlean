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
            if m:
                ids.append((m.group(1), sub))
        start += len(entries)
        time.sleep(3)
    return ids[:n]


def fetch_source(aid):
    url = f"https://export.arxiv.org/e-print/{aid}"
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


def main():
    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {}
    for era, (lo, hi) in ERAS.items():
        os.makedirs(os.path.join(OUT, era), exist_ok=True)
        for cat in CATS:
            print(f"[list] {era} {cat}", flush=True)
            for aid, sub in api_list(cat, lo, hi, PER_QUERY):
                safe = aid.replace("/", "_")
                dst = os.path.join(OUT, era, f"{safe}.tex")
                if os.path.exists(dst) or safe in manifest:
                    continue
                try:
                    text = fetch_source(aid)
                except Exception as ex:
                    print(f"[err] {aid}: {ex}", flush=True)
                    time.sleep(3)
                    continue
                time.sleep(3)
                if text is None:
                    manifest[safe] = dict(id=aid, era=era, cat=cat,
                                          submitted=sub, skipped=True)
                    continue
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(text)
                manifest[safe] = dict(id=aid, era=era, cat=cat, submitted=sub,
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

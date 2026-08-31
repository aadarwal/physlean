#!/usr/bin/env python3
"""Print the per-language content of results_cs/registration_gamma.json
without the SHA inventories that dominate the file."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "results_cs/registration_gamma.json"
d = json.load(open(path))

top = {k: v for k, v in d.items()
       if not isinstance(v, (dict, list)) and "sha" not in k}
print("top-level:", top)

langs = d.get("languages", d)
for k, v in langs.items():
    if not isinstance(v, dict):
        continue
    flat = {a: (round(b, 4) if isinstance(b, float) else b)
            for a, b in v.items()
            if not isinstance(b, (dict, list)) and "sha" not in a}
    print(f"\n{k}:")
    for a, b in sorted(flat.items()):
        print(f"   {a} = {b}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Split ARRL merged sections GeoJSON into one file per Division.

Input:  arrl_sections_merged.geojson (features with properties: division, section_name, section_code)
Output: GeoJSON files per division (e.g., arrl_division_Atlantic.geojson) in the chosen output directory
"""

import json
import sys
from pathlib import Path
import re
import unicodedata
from typing import Dict, List

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[-\s]+", "_", s)
    return s

def main():
    if len(sys.argv) < 3:
        print("Usage: python split_arrl_by_division.py <arrl_sections_merged.geojson> <output_dir>")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    with in_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)

    if fc.get("type") != "FeatureCollection":
        print("Error: Input is not a FeatureCollection GeoJSON.")
        sys.exit(2)

    features = fc.get("features", []) or []
    if not features:
        print("No features found in input.")
        sys.exit(0)

    # Group features by 'division'
    divisions: Dict[str, List[dict]] = {}
    missing_div_count = 0

    for feat in features:
        props = feat.get("properties", {}) or {}
        div = props.get("division")
        if not div:
            missing_div_count += 1
            continue
        divisions.setdefault(div, []).append(feat)

    if missing_div_count:
        print(f"Warning: {missing_div_count} features missing 'division' property and were skipped.")

    # Write one file per division
    written = 0
    for div_name, feats in divisions.items():
        fc_out = {"type": "FeatureCollection", "features": feats}
        fname = f"arrl_division_{slugify(div_name)}.geojson"
        out_path = out_dir / fname
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(fc_out, f)
        written += 1
        print(f"Wrote {out_path}  ({len(feats)} sections)")

    print(f"Done. Wrote {written} division files to: {out_dir}")

if __name__ == "__main__":
    main()

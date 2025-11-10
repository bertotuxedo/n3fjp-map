#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Merge Canadian Census Subdivision (CSD) polygons into sections/provinces for WFD mapping.

Input  : /mnt/data/georef-canada-census-subdivision.geojson
Output : ./canada_sections_merged.geojson (one feature per section code)

Sections produced:
- AB, BC, MB, NB, NL, NS, PE, QC, SK
- TER (NT+YT+NU combined)
- Ontario split: GH, ONE, ONS, ONN

Notes:
- You listed "Quebec (QB)"; the canonical two-letter is "QC". This script outputs QC.
- Name matching is case-insensitive and strips prefixes like "City of", "Regional Municipality of", etc.
"""

import json
import re
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

# --------- CONFIG ---------
INPUT = Path("/mnt/data/georef-canada-census-subdivision.geojson")
OUTPUT = Path("./canada_sections_merged.geojson")

# If your file has different columns, these helpers try to auto-detect.
PROV_CODE_CANDIDATES = [
    "prov_code", "PR_ABBR", "PRCODE", "PR_UID", "PRUID", "PROV_CODE", "PR", "PRCODE_ABBR"
]
PROV_NAME_CANDIDATES = ["PRNAME", "PRENAME", "province", "PROV_NAME", "PROV"]
CSD_NAME_CANDIDATES  = ["CSDNAME", "ENNAME", "name", "CSD_NAME", "CENSUS_SUBDIVISION_NAME"]

# Map PRUID (StatsCan numeric) to 2-letter abbreviations (fallback if no abbr present)
PRUID_TO_ABBR = {
    "10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON",
    "46": "MB", "47": "SK", "48": "AB", "59": "BC", "60": "YT", "61": "NT", "62": "NU"
}

# Ontario splits — name lists as provided (normalized automatically)
GH_NAMES = [
    "Durham Regional Municipality", "Halton Regional Municipality", "Hamilton",
    "Niagara Regional Municipality", "Peel Region Municipality", "Toronto",
    "York Regional Municipality"
]

ONE_NAMES = [
    "Frontenac County", "Hastings County", "Haliburton County", "City of Kawartha Lakes",
    "Lanark County", "Leeds & Grenville United Counties", "Lennox-Addington County",
    "Northumberland County", "City of Ottawa", "City of Prince Edward",
    "Peterborough County", "United Counties of Prescott & Russell", "Renfrew County",
    "United Counties of Stormont Dundas & Glengarry",
    "Nipissing District - South Part, Unorganized, South Algonquin",
]

ONN_NAMES = [
    "Algoma District", "Cochrane District", "Kenora District", "Manitoulin Island",
    "Nipissing District - All except South Part, Unorganized, South Algonquin",
    "Rainy River District", "City of Sudbury", "Grand Sudbury", "Thunder Bay District",
    "Timiskaming District",
]

ONS_NAMES = [
    "City of Brantford", "City of Brant", "Bruce County", "City of Chatham-Kent",
    "Dufferin County", "Elgin County", "Essex County", "Grey County",
    "Town of Haldimand", "Huron County", "Lambton County", "Middlesex County",
    "Muskoka District", "Town of Norfolk", "Oxford County", "Perth County",
    "Parry Sound District", "Simcoe County", "Waterloo Regional Municipality",
    "Wellington County",
]

# Territories that will be merged into TER
TERR_ABBRS = {"YT", "NT", "NU"}

# Provinces (kept as-is)
PROV_SETS = {"AB", "BC", "MB", "NB", "NL", "NS", "PE", "QC", "SK"}

# ---------- Helpers ----------
def normalize(s: str) -> str:
    """Normalize names for matching: lowercase, trim, collapse spaces, drop common prefixes."""
    if s is None:
        return ""
    t = s.strip().lower()

    # Drop “of” prefixes (City of, Town of, Regional Municipality of, United Counties of, County of, Municipality of)
    t = re.sub(r"^(city|town|regional municipality|united counties|county|municipality|township)\s+of\s+", "", t)
    t = t.replace("&", "and")
    # collapse whitespace and punctuation variants
    t = re.sub(r"[\s\-–—]+", " ", t)
    t = t.replace(",", "").strip()
    return t

def normalize_set(names):
    return {normalize(x) for x in names}

GH_N = normalize_set(GH_NAMES)
ONE_N = normalize_set(ONE_NAMES)
ONN_N = normalize_set(ONN_NAMES)
ONS_N = normalize_set(ONS_NAMES)

def pick_first_present(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None

def ensure_multipolygon(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (MultiPolygon,)):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    # If it's a GeometryCollection or others, dissolve polygons only
    polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))] if hasattr(geom, "geoms") else []
    if not polys:
        return None
    merged = polys[0]
    for g in polys[1:]:
        merged = merged.union(g)
    return ensure_multipolygon(merged)

# ---------- Load ----------
print(f"Reading {INPUT} ...")
gdf = gpd.read_file(INPUT)

cols = list(gdf.columns)
prov_code_col = pick_first_present(cols, PROV_CODE_CANDIDATES)
prov_name_col = pick_first_present(cols, PROV_NAME_CANDIDATES)
csd_name_col  = pick_first_present(cols, CSD_NAME_CANDIDATES)

if not csd_name_col:
    raise SystemExit(f"Could not detect CSD name column. Looked for {CSD_NAME_CANDIDATES}. Found: {cols}")

# Build a 2-letter province abbr column
if prov_code_col and gdf[prov_code_col].astype(str).str.len().eq(2).any():
    gdf["PR_ABBR2"] = gdf[prov_code_col].astype(str).str.upper()
else:
    # Try PRUID route
    pruid_col = None
    for c in cols:
        if c.upper() == "PRUID":
            pruid_col = c
            break
    if pruid_col:
        gdf["PR_ABBR2"] = gdf[pruid_col].astype(str).map(PRUID_TO_ABBR).fillna("")
    else:
        # Try to infer from full name
        name_to_abbr = {
            "newfoundland and labrador": "NL",
            "prince edward island": "PE",
            "nova scotia": "NS",
            "new brunswick": "NB",
            "quebec": "QC",
            "ontario": "ON",
            "manitoba": "MB",
            "saskatchewan": "SK",
            "alberta": "AB",
            "british columbia": "BC",
            "yukon": "YT",
            "northwest territories": "NT",
            "nunavut": "NU",
        }
        if not prov_name_col:
            raise SystemExit("Could not detect province code/name columns. Please adjust PROV_CODE_CANDIDATES/PROV_NAME_CANDIDATES.")
        gdf["PR_ABBR2"] = (
            gdf[prov_name_col]
            .astype(str)
            .str.lower()
            .map(name_to_abbr)
            .fillna("")
        )

if gdf["PR_ABBR2"].eq("").any():
    missing = gdf[gdf["PR_ABBR2"].eq("")][[csd_name_col]].head(5)
    print("Warning: some rows have unknown province abbr. Example:", missing.to_dict(orient="records"))

# Normalized CSD name for matching
gdf["_CSD_NORM"] = gdf[csd_name_col].astype(str).apply(normalize)

# ---------- Assign Section Codes ----------
def assign_section(row):
    pr = row["PR_ABBR2"]
    if pr in TERR_ABBRS:
        return "TER"
    if pr in PROV_SETS:
        return pr
    if pr == "ON":
        n = row["_CSD_NORM"]

        if n in GH_N:
            return "GH"
        if n in ONE_N:
            return "ONE"
        if n in ONN_N:
            return "ONN"
        if n in ONS_N:
            return "ONS"
        # Not explicitly listed → fall back to ONS (southern rest) by default
        # You can change this behavior if desired.
        return "ONS"

    # If somehow other codes exist, skip
    return None

gdf["SECTION"] = gdf.apply(assign_section, axis=1)
keep = gdf[~gdf["SECTION"].isna()].copy()

# ---------- Dissolve (merge) ----------
# Dissolve all geometries by SECTION
print("Merging geometries by SECTION ...")
merged = keep.dissolve(by="SECTION", as_index=False, aggfunc="first")

# Ensure MultiPolygon geometries and drop empties
merged["geometry"] = merged["geometry"].apply(ensure_multipolygon)
merged = merged[~merged["geometry"].isna()].copy()

# Add properties expected by your frontend/backend
# code: section code; name: readable
SECTION_LABELS = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland & Labrador",
    "NS": "Nova Scotia", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "TER": "Territories (YT+NT+NU)",
    "GH": "Ontario – Golden Horseshoe",
    "ONE": "Ontario East",
    "ONN": "Ontario North",
    "ONS": "Ontario South",
}
merged["code"] = merged["SECTION"]
merged["name"] = merged["SECTION"].map(SECTION_LABELS).fillna(merged["SECTION"])

# Order features in a friendly way
order = ["GH","ONE","ONN","ONS","AB","BC","MB","NB","NL","NS","PE","QC","SK","TER"]
merged["__order"] = merged["code"].apply(lambda c: order.index(c) if c in order else 999)
merged = merged.sort_values("__order").drop(columns="__order")

# ---------- Save ----------
merged = merged[["code", "name", "geometry"]]
merged.to_file(OUTPUT, driver="GeoJSON")
print(f"✅ Wrote {OUTPUT.resolve()}")

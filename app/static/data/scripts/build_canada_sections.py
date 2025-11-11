#!/usr/bin/env python3
import argparse
import sys
import unicodedata
import re
import geopandas as gpd
from shapely.geometry import GeometryCollection
from shapely.geometry.polygon import orient

# ---------------------------
# Normalization helpers
# ---------------------------
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def norm_name(s: str) -> str:
    if s is None:
        return ""
    x = strip_accents(str(s)).lower()
    # remove punctuation
    x = re.sub(r"[^\w\s&/]", " ", x)
    # collapse whitespace
    x = re.sub(r"\s+", " ", x).strip()

    # remove common words in ENG/FRA for fair matching
    stop = [
        "county","comte","regional municipality","municipalite regionale",
        "united counties","comtes unis","district","city of","ville de",
        "town of","cité de","municipality","municipalite","rm","rm of",
        "region","regions","united county","county of","comte de",
        "united","comtes","ville","city"
    ]
    for w in stop:
        x = re.sub(rf"\b{w}\b", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x

# Canonical set builder
def canon_set(names):
    return set(norm_name(n) for n in names)

# ---------------------------
# Ontario group definitions
# ---------------------------

# GH = Greater Horseshoe (CSD-level members)
GH_CSD = canon_set([
    "Durham Regional Municipality",
    "Halton Regional Municipality",
    "City of Hamilton",
    "Niagara Regional Municipality",
    "Peel Regional Municipality",
    "City of Toronto",
    "York Regional Municipality",
    # French-ish forms likely present for Ontario as well:
    "Municipalité régionale de Durham",
    "Municipalité régionale de Halton",
    "Hamilton",
    "Municipalité régionale de Niagara",
    "Municipalité régionale de Peel",
    "Toronto",
    "Municipalité régionale de York",
])

# ONE (mostly CD-level — Counties, United Counties, City of Ottawa etc.)
ONE_CD = canon_set([
    "Frontenac County",
    "Hastings County",
    "Haliburton County",
    "City of Kawartha Lakes",  # single-tier
    "Lanark County",
    "United Counties of Leeds and Grenville",
    "Lennox and Addington County",
    "Northumberland County",
    "City of Ottawa",          # single-tier
    "Prince Edward County",
    "Peterborough County",
    "United Counties of Prescott and Russell",
    "Renfrew County",
    "United Counties of Stormont, Dundas and Glengarry",
    # French variants that might appear:
    "Comte de Frontenac","Comte de Hastings","Comte de Haliburton",
    "Ville de Kawartha Lakes","Comte de Lanark",
    "Comtes unis de Leeds et Grenville",
    "Comte de Lennox et Addington",
    "Comte de Northumberland","Ville d Ottawa",
    "Comte de Prince Edward","Comte de Peterborough",
    "Comtes unis de Prescott et Russell",
    "Comte de Renfrew",
    "Comtes unis de Stormont Dundas et Glengarry",
])

# ONN (CD-level — northern districts and City of Greater Sudbury single-tier)
ONN_CD = canon_set([
    "Algoma District",
    "Cochrane District",
    "Kenora District",
    "Manitoulin District",
    "Nipissing District",  # note: “South Part, Unorganized, South Algonquin” excluded (see special below)
    "Rainy River District",
    "City of Greater Sudbury",
    "Thunder Bay District",
    "Timiskaming District",
    # French variants:
    "District d algoma","District de cochrane","District de kenora",
    "District de manitoulin","District de nipissing",
    "District de rainy river","Ville du Grand Sudbury",
    "District de thunder bay","District de timiskaming",
])

# ONS (CD-level — southwest/central)
ONS_CD = canon_set([
    "City of Brantford",
    "Brant County",
    "Bruce County",
    "Chatham-Kent",
    "Dufferin County",
    "Elgin County",
    "Essex County",
    "Grey County",
    "Haldimand County",
    "Huron County",
    "Lambton County",
    "Middlesex County",
    "District Municipality of Muskoka",
    "Norfolk County",
    "Oxford County",
    "Perth County",
    "Parry Sound District",
    "Simcoe County",
    "Regional Municipality of Waterloo",
    "Wellington County",
    # French-ish:
    "Ville de brantford","Comte de brant","Comte de bruce",
    "Chatham kent","Comte de dufferin","Comte d elgin",
    "Comte d essex","Comte de grey","Comte d haldimand",
    "Comte de huron","Comte de lambton","Comte de middlesex",
    "Municipalite de district de muskoka","Comte de norfolk",
    "Comte d oxford","Comte de perth","District de parry sound",
    "Comte de simcoe","Municipalite regionale de waterloo",
    "Comte de wellington",
])

# Special Ontario clause:
# “Nipissing District - South Part, Unorganized, South Algonquin” must be in ONE (not ONN).
# We'll detect this by *CSD* name match (csd_name_fr) containing "South Algonquin".
SPECIAL_ONE_CSD_FRAGMENT = canon_set(["South Algonquin", "Algonquin Sud"])

# ---------------------------
# Province/territory to SECTION mapping (outside Ontario)
# ---------------------------
# Use French province names as seen in your file (prov_name_fr).
PROV_TO_SECTION = {
    # Provinces
    "alberta": "AB",
    "colombie britanique": "BC",    # sometimes “Colombie-Britannique”
    "colombie britanique": "BC",
    "colombie britannique": "BC",
    "colombie-britannique": "BC",
    "manitoba": "MB",
    "nouveau brunswick": "NB",
    "terre neuve et labrador": "NL",
    "nouvelle ecosse": "NS",
    "nouvelle-ecosse": "NS",
    "ile du prince edouard": "PE",
    "ile-du-prince-edouard": "PE",
    "quebec": "QC",
    "saskatchewan": "SK",

    # Territories (mapped later to TER with union)
    "territoires du nord ouest": "TER",
    "territoires du nord-ouest": "TER",
    "yukon": "TER",
    "nunavut": "TER",
}

# English fallbacks (if your file sometimes carries English)
PROV_TO_SECTION_EN = {
    "alberta": "AB",
    "british columbia": "BC",
    "manitoba": "MB",
    "new brunswick": "NB",
    "newfoundland and labrador": "NL",
    "nova scotia": "NS",
    "prince edward island": "PE",
    "quebec": "QC",
    "saskatchewan": "SK",
    "northwest territories": "TER",
    "yukon": "TER",
    "nunavut": "TER",
}

def pick_section_non_ontario(prov_name: str) -> str | None:
    p = norm_name(prov_name)
    if p in PROV_TO_SECTION:
        return PROV_TO_SECTION[p]
    if p in PROV_TO_SECTION_EN:
        return PROV_TO_SECTION_EN[p]
    return None

# ---------------------------
# Assignment logic for Ontario
# ---------------------------
def assign_ontario_section(row, cd_name_col: str, csd_name_col: str) -> str | None:
    cd_name = norm_name(row.get(cd_name_col, ""))
    csd_name = norm_name(row.get(csd_name_col, ""))

    # Special carve-out -> ONE (South Algonquin)
    for frag in SPECIAL_ONE_CSD_FRAGMENT:
        if frag in csd_name:
            return "ONE"

    # GH by CSD (municipalities at CSD level)
    if csd_name in GH_CSD:
        return "GH"

    # The rest are mostly CD-based
    if cd_name in ONE_CD:
        return "ONE"
    if cd_name in ONN_CD:
        return "ONN"
    if cd_name in ONS_CD:
        return "ONS"

    return None  # unassigned -> will default later if needed

# ---------------------------
# Main
# ---------------------------
def main():
    ap = argparse.ArgumentParser(description="Build merged Canadian sections GeoJSON (WFD/RAC style).")
    ap.add_argument("--input", required=True, help="Path to source CSD-level GeoJSON")
    ap.add_argument("--output", required=True, help="Path to write merged GeoJSON")
    args = ap.parse_args()

    print(f"Reading {args.input} ...")
    gdf = gpd.read_file(args.input)

    # Identify columns present
    cols = {c.lower(): c for c in gdf.columns}
    # Province name (French/English)
    prov_col = cols.get("prov_name_fr") or cols.get("prov_name_en") or cols.get("prname") or cols.get("province") or None
    # CD/CSD names
    cd_col = cols.get("cd_name_fr") or cols.get("cd_name_en") or cols.get("cdname") or None
    csd_col = cols.get("csd_name_fr") or cols.get("csd_name_en") or cols.get("csdname") or cols.get("name") or None

    if not prov_col:
        print(f"ERROR: could not detect province name column. Found: {list(gdf.columns)}", file=sys.stderr)
        sys.exit(1)
    if not csd_col:
        print(f"ERROR: could not detect CSD name column. Found: {list(gdf.columns)}", file=sys.stderr)
        sys.exit(1)
    if not cd_col:
        print("WARN: no CD name column detected; Ontario matching will be CSD-only where possible.")

    # Prepare SECTION
    gdf["SECTION"] = None

    # First, provinces that are NOT Ontario
    mask_not_on = gdf[prov_col].str.lower() != "ontario"
    gdf.loc[mask_not_on, "SECTION"] = gdf.loc[mask_not_on, prov_col].apply(pick_section_non_ontario)

    # Ontario rows
    on_mask = gdf[prov_col].str.lower() == "ontario"
    if on_mask.any():
        on_rows = gdf.loc[on_mask].copy()
        # assign with Ontario logic
        gdf.loc[on_mask, "SECTION"] = on_rows.apply(
            lambda r: assign_ontario_section(r, cd_col if cd_col else "", csd_col), axis=1
        )

        # Any Ontario rows still unassigned? Default them to ONS (safer than losing them)
        still_none = on_mask & gdf["SECTION"].isna()
        if still_none.any():
            gdf.loc[still_none, "SECTION"] = "ONS"

    # Collapse/clean: Anything still None -> drop (shouldn’t be many)
    gdf = gdf[~gdf["SECTION"].isna()].copy()

    # For TER: territories already mapped to "TER" above; nothing special needed here
    # Dissolve by SECTION
    print("Merging geometries by SECTION ...")
    out = gdf.dissolve(by="SECTION", as_index=False)

    # Some dissolve results may carry GeometryCollections; make valid via buffer(0)
    def make_valid(geom):
        if geom is None:
            return GeometryCollection()
        if not geom.is_valid:
            try:
                geom = geom.buffer(0)
            except Exception:
                return GeometryCollection()
        return geom

    def orient_geom(geom):
        if geom is None or geom.is_empty:
            return geom
        gtype = geom.geom_type
        try:
            if gtype == "Polygon":
                return orient(geom, sign=1.0)
            if gtype == "MultiPolygon":
                return type(geom)(orient(part, sign=1.0) for part in geom.geoms)
        except Exception:
            return geom
        return geom

    out["geometry"] = out["geometry"].apply(lambda geom: orient_geom(make_valid(geom)))

    # Save
    print(f"✅ Wrote {args.output}")
    out.to_file(args.output, driver="GeoJSON")

    # Quick summary
    counts = gdf.groupby("SECTION").size().sort_values(ascending=False)
    print("\nFeature counts by SECTION (pre-dissolve):")
    print(counts.to_string())


if __name__ == "__main__":
    main()

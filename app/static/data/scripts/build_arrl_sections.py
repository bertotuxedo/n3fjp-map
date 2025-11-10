#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Builds a dissolved ARRL Sections GeoJSON from a U.S. county GeoJSON.
Output fields: division, section_name, section_code
"""

import json, re, unicodedata, sys, time
from pathlib import Path

# Shapely-only build (no GeoPandas needed)
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import unary_union

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = s.replace("&", "and").replace("–","-").replace("—","-").replace(".", "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bst[ .]\b", "saint ", s)  # St. -> Saint (common county variant)
    s = s.replace("'", "")
    return s

def county_key(state, county):
    return (norm(state), norm(county))

def load_counties(path: Path):
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    idx = {}
    for feat in data["features"]:
        props = feat.get("properties", {})
        st = props.get("state_name") or props.get("STATE_NAME") or props.get("STATE")
        ct = props.get("name") or props.get("NAME") or props.get("county_name") or props.get("COUNTY")
        if st and ct:
            idx[county_key(st, ct)] = feat["geometry"]
    return idx

def sec(lst, code, name, division, states=None, counties=None):
    lst.append({"code": code, "name": name, "division": division,
                "states": states or [], "counties": counties or []})

def define_sections():
    S = []

    # ---------- Atlantic ----------
    sec(S,"DE","Delaware","Atlantic",states=["Delaware"])
    sec(S,"EPA","Eastern Pennsylvania","Atlantic",counties=[("Pennsylvania", c) for c in [
        "Adams","Berks","Bradford","Bucks","Carbon","Chester","Columbia","Cumberland","Dauphin","Delaware","Juniata",
        "Lackawanna","Lancaster","Lebanon","Lehigh","Luzerne","Lycoming","Monroe","Montgomery","Montour","Northampton",
        "Northumberland","Perry","Philadelphia","Pike","Schuylkill","Snyder","Sullivan","Susquehanna","Tioga","Union",
        "Wayne","Wyoming","York"
    ]])
    sec(S,"WPA","Western Pennsylvania","Atlantic",counties=[("Pennsylvania", c) for c in [
        "Allegheny","Armstrong","Beaver","Bedford","Blair","Butler","Cambria","Cameron","Centre","Clarion","Clearfield",
        "Clinton","Crawford","Elk","Erie","Fayette","Forest","Franklin","Fulton","Greene","Huntingdon","Indiana",
        "Jefferson","Lawrence","McKean","Mercer","Mifflin","Potter","Somerset","Venango","Warren","Washington","Westmoreland"
    ]])
    sec(S,"MDC","Maryland-DC","Atlantic",states=["Maryland","District of Columbia"])
    sec(S,"NNY","Northern New York","Atlantic",counties=[("New York", c) for c in [
        "Clinton","Essex","Franklin","Fulton","Hamilton","Jefferson","Lewis","Montgomery","St. Lawrence","Schoharie"
    ]])
    sec(S,"SNJ","Southern New Jersey","Atlantic",counties=[("New Jersey", c) for c in [
        "Atlantic","Burlington","Camden","Cape May","Cumberland","Gloucester","Mercer","Ocean","Salem"
    ]])
    sec(S,"WNY","Western New York","Atlantic",counties=[("New York", c) for c in [
        "Allegany","Broome","Cattaraugus","Cayuga","Chautauqua","Chemung","Chenango","Cortland","Delaware","Erie",
        "Genesee","Herkimer","Livingston","Madison","Monroe","Niagara","Oneida","Onondaga","Ontario","Orleans",
        "Oswego","Otsego","Schuyler","Seneca","Steuben","Tioga","Tompkins","Wayne","Wyoming","Yates"
    ]])

    # ---------- Central ----------
    for code, st in [("IL","Illinois"),("IN","Indiana"),("WI","Wisconsin")]:
        sec(S, code, st, "Central", states=[st])

    # ---------- Dakota ----------
    for code, st in [("MN","Minnesota"),("ND","North Dakota"),("SD","South Dakota")]:
        sec(S, code, st, "Dakota", states=[st])

    # ---------- Delta ----------
    for code, st in [("AR","Arkansas"),("LA","Louisiana"),("MS","Mississippi"),("TN","Tennessee")]:
        sec(S, code, st, "Delta", states=[st])

    # ---------- Great Lakes ----------
    for code, st in [("KY","Kentucky"),("MI","Michigan"),("OH","Ohio")]:
        sec(S, code, st, "Great Lakes", states=[st])

    # ---------- Hudson ----------
    sec(S,"ENY","Eastern New York","Hudson",counties=[("New York", c) for c in [
        "Albany","Columbia","Dutchess","Greene","Orange","Putnam","Rensselaer","Rockland","Saratoga",
        "Schenectady","Sullivan","Ulster","Warren","Washington","Westchester"
    ]])
    sec(S,"NLI","New York City-Long Island","Hudson",counties=[("New York", c) for c in [
        "Bronx","Kings","Nassau","New York","Queens","Richmond","Suffolk"
    ]])
    sec(S,"NNJ","Northern New Jersey","Hudson",counties=[("New Jersey", c) for c in [
        "Bergen","Essex","Hudson","Hunterdon","Middlesex","Monmouth","Morris","Passaic","Somerset","Sussex","Union","Warren"
    ]])

    # ---------- Midwest ----------
    for code, st in [("IA","Iowa"),("KS","Kansas"),("MO","Missouri"),("NE","Nebraska")]:
        sec(S, code, st, "Midwest", states=[st])

    # ---------- New England ----------
    sec(S,"CT","Connecticut","New England",states=["Connecticut"])
    sec(S,"EMA","Eastern Massachusetts","New England",counties=[("Massachusetts", c) for c in [
        "Barnstable","Bristol","Dukes","Essex","Middlesex","Nantucket","Norfolk","Plymouth","Suffolk"
    ]])
    for code, st in [("ME","Maine"),("NH","New Hampshire"),("RI","Rhode Island"),("VT","Vermont")]:
        sec(S, code, st, "New England", states=[st])
    sec(S,"WMA","Western Massachusetts","New England",counties=[("Massachusetts", c) for c in [
        "Berkshire","Franklin","Hampden","Hampshire","Worcester"
    ]])

    # ---------- Northwestern ----------
    sec(S,"AK","Alaska","Northwestern",states=["Alaska"])
    sec(S,"EWA","Eastern Washington","Northwestern",counties=[("Washington", c) for c in [
        "Adams","Asotin","Benton","Chelan","Columbia","Douglas","Ferry","Franklin","Garfield","Grant","Kittitas",
        "Klickitat","Lincoln","Okanogan","Pend Oreille","Spokane","Stevens","Walla Walla","Whitman","Yakima"
    ]])
    for code, st in [("ID","Idaho"),("MT","Montana"),("OR","Oregon")]:
        sec(S, code, st, "Northwestern", states=[st])
    sec(S,"WWA","Western Washington","Northwestern",counties=[("Washington", c) for c in [
        "Clallam","Clark","Cowlitz","Grays Harbor","Island","Jefferson","King","Kitsap","Lewis","Mason","Pacific",
        "Pierce","San Juan","Skagit","Skamania","Snohomish","Thurston","Wahkiakum","Whatcom"
    ]])

    # ---------- Pacific ----------
    sec(S,"EB","East Bay","Pacific",counties=[("California", c) for c in ["Alameda","Contra Costa","Napa","Solano"]])
    sec(S,"NV","Nevada","Pacific",states=["Nevada"])
    sec(S,"PAC","Pacific (Hawaii)","Pacific",states=["Hawaii"])  # CAUTION: county file won't include other Pacific territories
    sec(S,"SV","Sacramento Valley","Pacific",counties=[("California", c) for c in [
        "Alpine","Amador","Butte","Colusa","El Dorado","Glenn","Lassen","Modoc","Nevada","Placer","Plumas",
        "Sacramento","Shasta","Sierra","Siskiyou","Sutter","Tehama","Trinity","Yolo","Yuba"
    ]])
    sec(S,"SF","San Francisco","Pacific",counties=[("California", c) for c in [
        "Del Norte","Humboldt","Lake","Marin","Mendocino","San Francisco","Sonoma"
    ]])
    sec(S,"SJV","San Joaquin Valley","Pacific",counties=[("California", c) for c in [
        "Calaveras","Fresno","Kern","Kings","Madera","Mariposa","Merced","Mono","San Joaquin","Stanislaus","Tulare","Tuolumne"
    ]])
    sec(S,"SCV","Santa Clara Valley","Pacific",counties=[("California", c) for c in [
        "Monterey","San Benito","San Mateo","Santa Clara","Santa Cruz"
    ]])

    # ---------- Roanoke ----------
    for code, st in [("NC","North Carolina"),("SC","South Carolina"),("VA","Virginia"),("WV","West Virginia")]:
        sec(S, code, st, "Roanoke", states=[st])

    # ---------- Rocky Mountain ----------
    for code, st in [("CO","Colorado"),("NM","New Mexico"),("UT","Utah"),("WY","Wyoming")]:
        sec(S, code, st, "Rocky Mountain", states=[st])

    # ---------- Southeastern ----------
    for code, st in [("AL","Alabama"),("GA","Georgia")]:
        sec(S, code, st, "Southeastern", states=[st])
    sec(S,"NFL","Northern Florida","Southeastern",counties=[("Florida", c) for c in [
        "Alachua","Baker","Bay","Bradford","Calhoun","Citrus","Clay","Columbia","Dixie","Duval","Escambia","Flagler","Franklin",
        "Gadsden","Gilchrist","Gulf","Hamilton","Hernando","Holmes","Jackson","Jefferson","Lafayette","Lake","Leon","Levy",
        "Liberty","Madison","Marion","Nassau","Okaloosa","Orange","Putnam","Santa Rosa","Seminole","St. Johns","Sumter",
        "Suwannee","Taylor","Union","Volusia","Wakulla","Walton","Washington"
    ]])
    sec(S,"SFL","Southern Florida","Southeastern",counties=[("Florida", c) for c in [
        "Brevard","Broward","Collier","Miami-Dade","Glades","Hendry","Indian River","Lee","Martin","Monroe","Okeechobee",
        "Osceola","Palm Beach","St. Lucie"
    ]])
    sec(S,"WCF","West Central Florida","Southeastern",counties=[("Florida", c) for c in [
        "Charlotte","DeSoto","Hardee","Highlands","Hillsborough","Manatee","Pasco","Pinellas","Polk","Sarasota"
    ]])
    sec(S,"PR","Puerto Rico","Southeastern",states=["Puerto Rico"])
    sec(S,"VI","US Virgin Islands","Southeastern",states=["United States Virgin Islands"])

    # ---------- Southwestern ----------
    sec(S,"AZ","Arizona","Southwestern",states=["Arizona"])
    sec(S,"LAX","Los Angeles","Southwestern",counties=[("California","Los Angeles")])
    sec(S,"ORG","Orange","Southwestern",counties=[("California", c) for c in ["Inyo","Orange","Riverside","San Bernardino"]])
    sec(S,"SDG","San Diego","Southwestern",counties=[("California", c) for c in ["Imperial","San Diego"]])
    sec(S,"SB","Santa Barbara","Southwestern",counties=[("California", c) for c in ["San Luis Obispo","Santa Barbara","Ventura"]])

    # ---------- West Gulf ----------
    sec(S,"OK","Oklahoma","West Gulf",states=["Oklahoma"])
    sec(S,"NTX","North Texas","West Gulf",counties=[("Texas", c) for c in [
        "Anderson","Archer","Baylor","Bell","Bosque","Bowie","Brown","Camp","Cass","Cherokee","Clay","Collin","Comanche",
        "Cooke","Coryell","Dallas","Delta","Denton","Eastland","Ellis","Erath","Falls","Fannin","Franklin","Freestone",
        "Grayson","Gregg","Hamilton","Harrison","Henderson","Hill","Hood","Hopkins","Hunt","Jack","Johnson","Kaufman",
        "Lamar","Lampasas","Limestone","McLennan","Marion","Mills","Montague","Morris","Nacogdoches","Navarro","Palo Pinto",
        "Panola","Parker","Rains","Red River","Rockwall","Rusk","Shelby","Smith","Somervell","Stephens","Tarrant",
        "Throckmorton","Titus","Upshur","Van Zandt","Wichita","Wilbarger","Wise","Wood","Young"
    ]])
    sec(S,"STX","South Texas","West Gulf",counties=[("Texas", c) for c in [
        "Angelina","Aransas","Atascosa","Austin","Bandera","Bastrop","Bee","Bexar","Blanco","Brazoria","Brazos","Brooks",
        "Burleson","Burnet","Caldwell","Calhoun","Cameron","Chambers","Colorado","Comal","Concho","DeWitt","Dimmit","Duval",
        "Edwards","Fayette","Fort Bend","Frio","Galveston","Gillespie","Goliad","Gonzales","Grimes","Guadalupe","Hardin",
        "Harris","Hays","Hidalgo","Houston","Jackson","Jasper","Jefferson","Jim Hogg","Jim Wells","Karnes","Kendall",
        "Kenedy","Kerr","Kimble","Kinney","Kleberg","La Salle","Lavaca","Lee","Leon","Liberty","Live Oak","Llano","Madison",
        "Mason","Matagorda","Maverick","McCulloch","McMullen","Medina","Menard","Milam","Montgomery","Newton","Nueces",
        "Orange","Polk","Real","Refugio","Robertson","Sabine","San Augustine","San Jacinto","San Patricio","San Saba","Starr",
        "Travis","Trinity","Tyler","Uvalde","Val Verde","Victoria","Walker","Waller","Washington","Webb","Wharton","Willacy",
        "Williamson","Wilson","Zapata","Zavala"
    ]])
    sec(S,"WTX","West Texas","West Gulf",counties=[("Texas", c) for c in [
        "Andrews","Armstrong","Bailey","Borden","Brewster","Briscoe","Callahan","Carson","Castro","Childress","Cochran",
        "Coke","Coleman","Collingsworth","Cottle","Crane","Crockett","Crosby","Culberson","Dallam","Dawson","Deaf Smith",
        "Dickens","Donley","Ector","El Paso","Fisher","Floyd","Foard","Gaines","Garza","Glasscock","Gray","Hale","Hall",
        "Hansford","Hardeman","Hartley","Haskell","Hemphill","Hockley","Howard","Hudspeth","Hutchinson","Irion","Jeff Davis",
        "Jones","Kent","King","Knox","Lamb","Lipscomb","Loving","Lubbock","Lynn","Martin","Midland","Mitchell","Moore",
        "Motley","Nolan","Ochiltree","Oldham","Parmer","Pecos","Potter","Presidio","Randall","Reagan","Reeves","Roberts",
        "Runnels","Schleicher","Scurry","Shackelford","Sherman","Sterling","Stonewall","Sutton","Swisher","Taylor","Terrell",
        "Terry","Tom Green","Upton","Ward","Wheeler","Winkler","Yoakum"
    ]])

    return S

def main():
    if len(sys.argv) < 3:
        print("Usage: python build_arrl_sections.py <input_counties.geojson> <output_sections.geojson>")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    print("Loading counties…")
    county_index = load_counties(in_path)

    print("Defining sections…")
    sections = define_sections()

    # Precompute needed county shapes only
    print("Collecting county geometries…")
    needed_keys = set()
    for s in sections:
        for st in s["states"]:
            stn = norm(st)
            for (st_key, ct_key) in county_index.keys():
                if st_key == stn:
                    needed_keys.add((st_key, ct_key))
        for st, ct in s["counties"]:
            needed_keys.add(county_key(st, ct))

    shape_index = {}
    for k in needed_keys:
        geom = county_index.get(k)
        if geom:
            # Optional: tweak tolerance for speed/size tradeoff
            shp = shape(geom) # .simplify(0.002, preserve_topology=True)
            shape_index[k] = shp

    print("Dissolving per section…")
    out_features = []
    for s in sections:
        geoms = []
        if s["states"]:
            wanted = set(norm(x) for x in s["states"])
            for (st_key, ct_key), shp in shape_index.items():
                if st_key in wanted:
                    geoms.append(shp)
        if s["counties"]:
            for st, ct in s["counties"]:
                shp = shape_index.get(county_key(st, ct))
                if shp is not None:
                    geoms.append(shp)
        if not geoms:
            # Territories missing from county file (e.g., non-county possessions)
            continue

        merged = unary_union(geoms)
        if isinstance(merged, Polygon):
            merged = MultiPolygon([merged])

        out_features.append({
            "type": "Feature",
            "geometry": mapping(merged),
            "properties": {
                "division": s["division"],
                "section_name": s["name"],
                "section_code": s["code"],
            },
        })

    out = {"type": "FeatureCollection", "features": out_features}
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out, f)

    print(f"Done. Wrote {len(out_features)} sections -> {out_path}")

if __name__ == "__main__":
    main()

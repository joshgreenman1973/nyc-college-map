#!/usr/bin/env python3
"""
Fetch NYC college/university enrollment data from the Urban Institute
Education Data API (free, no key required), built on IPEDS.

Output: data/campuses.json  -- one record per institution with location,
control type, undergraduate vs graduate/professional headcount, full-time/
part-time split, sex split, and race/ethnicity breakdown.

Data source: Urban Institute Education Data Portal
  https://educationdata.urban.org/  (IPEDS, fall enrollment, year 2022)
"""

import json
import urllib.request
import urllib.error
import time
import os

YEAR = 2022
BASE = "https://educationdata.urban.org/api/v1/college-university/ipeds"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# NYC five-borough county FIPS codes
NYC_COUNTIES = {
    36005: "Bronx",
    36047: "Brooklyn",
    36061: "Manhattan",
    36081: "Queens",
    36085: "Staten Island",
}

# IPEDS control of institution
CONTROL = {1: "Public", 2: "Private nonprofit", 3: "Private for-profit"}

# IPEDS race/ethnicity codes (Urban Institute fall-enrollment coding)
RACE = {
    1: "White",
    2: "Black",
    3: "Hispanic",
    4: "Asian",
    5: "American Indian / Alaska Native",
    6: "Native Hawaiian / Pacific Islander",
    7: "Two or more races",
    8: "U.S. Nonresident (international)",
    9: "Race/ethnicity unknown",
}

LEVELS = {1: "undergrad", 2: "grad"}  # 1 = Undergraduate, 2 = Graduate/professional


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  retry {attempt+1} ({e})")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def fetch_directory():
    """Institution directory (name, location, control) for the 5 boroughs."""
    out = {}
    for fips_county, borough in NYC_COUNTIES.items():
        url = f"{BASE}/directory/{YEAR}/?fips=36&county_fips={fips_county}"
        data = get(url)
        for r in data["results"]:
            if r.get("latitude") in (None, 0) or r.get("longitude") in (None, 0):
                continue
            out[r["unitid"]] = {
                "unitid": r["unitid"],
                "name": r["inst_name"],
                "borough": borough,
                "address": r.get("address"),
                "zip": r.get("zip"),
                "lat": r["latitude"],
                "lon": r["longitude"],
                "control": CONTROL.get(r.get("inst_control"), "Other"),
                "offering_undergrad": r.get("offering_undergrad"),
                "offering_grad": r.get("offering_grad"),
                "medical_degree": r.get("medical_degree") == 1,
                "url": (r.get("url_school") or "").strip(),
            }
        print(f"  {borough}: {len(data['results'])} listed")
    print(f"directory: {len(out)} geolocated NYC institutions")
    return out


def fetch_level(level):
    """One statewide call per level: race breakdown + FT/PT + sex totals."""
    common = "class_level=99&degree_seeking=99"
    # 1) all races (omit race), sex total, ftpt total -> grand total + race split
    races = get(f"{BASE}/fall-enrollment/{YEAR}/{level}/race/sex/"
                f"?fips=36&sex=99&ftpt=99&{common}")["results"]
    # 2) full-time, 3) part-time
    ft = get(f"{BASE}/fall-enrollment/{YEAR}/{level}/race/sex/"
             f"?fips=36&race=99&sex=99&ftpt=1&{common}")["results"]
    pt = get(f"{BASE}/fall-enrollment/{YEAR}/{level}/race/sex/"
             f"?fips=36&race=99&sex=99&ftpt=2&{common}")["results"]
    # 4) men, 5) women
    men = get(f"{BASE}/fall-enrollment/{YEAR}/{level}/race/sex/"
              f"?fips=36&race=99&sex=1&ftpt=99&{common}")["results"]
    women = get(f"{BASE}/fall-enrollment/{YEAR}/{level}/race/sex/"
                f"?fips=36&race=99&sex=2&ftpt=99&{common}")["results"]

    per = {}

    def slot(unitid):
        return per.setdefault(unitid, {
            "total": 0, "ft": 0, "pt": 0, "men": 0, "women": 0, "race": {}
        })

    for r in races:
        e = r.get("enrollment_fall")
        if not e or e < 0:
            continue
        s = slot(r["unitid"])
        if r["race"] == 99:
            s["total"] = e
        else:
            label = RACE.get(r["race"])
            if label:
                s["race"][label] = s["race"].get(label, 0) + e

    def add(rows, key):
        for r in rows:
            e = r.get("enrollment_fall")
            if e and e > 0:
                slot(r["unitid"])[key] = e

    add(ft, "ft")
    add(pt, "pt")
    add(men, "men")
    add(women, "women")
    print(f"  level {level} ({LEVELS[level]}): {len(per)} institutions with data")
    return per


def main():
    print("Fetching directory...")
    directory = fetch_directory()

    print("Fetching undergraduate enrollment...")
    ug = fetch_level(1)
    print("Fetching graduate/professional enrollment...")
    gr = fetch_level(2)

    campuses = []
    for unitid, info in directory.items():
        u = ug.get(unitid, {})
        g = gr.get(unitid, {})
        ug_total = u.get("total", 0)
        gr_total = g.get("total", 0)
        total = ug_total + gr_total
        if total <= 0:
            continue  # drop institutions with no reported enrollment
        info.update({
            "undergrad": ug_total,
            "grad": gr_total,
            "total": total,
            "ug_ft": u.get("ft", 0), "ug_pt": u.get("pt", 0),
            "gr_ft": g.get("ft", 0), "gr_pt": g.get("pt", 0),
            "ug_men": u.get("men", 0), "ug_women": u.get("women", 0),
            "gr_men": g.get("men", 0), "gr_women": g.get("women", 0),
            "ug_race": u.get("race", {}),
            "gr_race": g.get("race", {}),
        })
        campuses.append(info)

    campuses.sort(key=lambda c: c["total"], reverse=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "year": YEAR,
        "source": "Urban Institute Education Data Portal (IPEDS fall enrollment)",
        "count": len(campuses),
        "campuses": campuses,
    }
    out_path = os.path.join(DATA_DIR, "campuses.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    tot_ug = sum(c["undergrad"] for c in campuses)
    tot_gr = sum(c["grad"] for c in campuses)
    print(f"\nWrote {len(campuses)} campuses -> {out_path}")
    print(f"Total undergrad: {tot_ug:,}   total grad/professional: {tot_gr:,}")
    print("Top 8 by enrollment:")
    for c in campuses[:8]:
        print(f"  {c['name'][:42]:42} ug={c['undergrad']:>6,} gr={c['grad']:>6,}")


if __name__ == "__main__":
    main()

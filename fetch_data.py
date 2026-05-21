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
import io
import csv
import zipfile

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

# IPEDS completions: Urban Institute award_level codes (validated against known
# single-field schools). Graduate / professional DEGREE levels only:
GRAD_AWARD_LEVELS = {9: "Master's", 22: "Doctorate (research)",
                     23: "Doctorate (professional practice)", 24: "Doctorate (other)"}
DEGREE_YEAR = 2022  # IPEDS completions collection (degrees conferred)

# CIP 2-digit family -> field-of-study label (the "type of professional school")
CIP_FAMILY = {
    1: "Agriculture", 3: "Natural resources", 4: "Architecture",
    5: "Area, ethnic & gender studies", 9: "Communication & journalism",
    10: "Communications technology", 11: "Computer & information sciences",
    12: "Personal & culinary services", 13: "Education", 14: "Engineering",
    15: "Engineering technology", 16: "Foreign languages & linguistics",
    19: "Family & consumer sciences", 22: "Law & legal studies",
    23: "English language & literature", 24: "Liberal arts & humanities",
    25: "Library science", 26: "Biological & biomedical sciences",
    27: "Mathematics & statistics", 29: "Military sciences",
    30: "Multi / interdisciplinary studies", 31: "Parks, recreation & fitness",
    38: "Philosophy & religious studies", 39: "Theology & religious vocations",
    40: "Physical sciences", 41: "Science technologies", 42: "Psychology",
    43: "Homeland security & law enforcement",
    44: "Public administration & social work", 45: "Social sciences",
    49: "Transportation", 50: "Visual & performing arts",
    51: "Health professions & medicine", 52: "Business & management",
    54: "History", 60: "Health residency programs",
}


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  retry {attempt+1} ({e})")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def get_all(url):
    """Follow pagination, returning all result rows."""
    out, u = [], url
    while u:
        d = get(u)
        out += d.get("results", [])
        u = d.get("next")
    return out


# IPEDS Fall Enrollment, Distance Education (EF...A_DIST). EFDELEV: 1 = all
# students, 2 = undergraduate, 12 = graduate/professional. Columns of interest:
# EFDETOT total, EFDEEXC enrolled exclusively in distance ed, EFDESOM some,
# EFDENON none. Distributed by NCES as a downloadable file (not in the Urban API).
def load_distance_education():
    url = f"https://nces.ed.gov/ipeds/datacenter/data/EF{YEAR}A_DIST.zip"
    raw = urllib.request.urlopen(url, timeout=120).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    fn = next((n for n in names if n.lower().endswith("_rv.csv")), names[0])
    out = {}
    with zf.open(fn) as fh:
        rdr = csv.reader(io.TextIOWrapper(fh, encoding="latin-1"))
        next(rdr)
        for row in rdr:
            try:
                uid, lev = int(row[0]), int(row[1])
            except (ValueError, IndexError):
                continue
            if lev not in (1, 2, 12):
                continue

            def n(i):
                try:
                    return int(row[i])
                except (ValueError, IndexError):
                    return 0
            out.setdefault(uid, {})[lev] = {"tot": n(3), "exc": n(5), "som": n(7), "non": n(9)}
    print(f"distance-ed: {len(out)} institutions loaded ({fn})")
    return out


def online_agg(dist, units, lev):
    a = {"tot": 0, "exc": 0, "som": 0, "non": 0}
    for u in units:
        d = dist.get(u, {}).get(lev)
        if d:
            for k in a:
                a[k] += d[k]
    return a


# Major U.S. cities to compare against New York City, defined city-proper by
# the IPEDS mailing-address city (NYC itself is the five-borough total).
# (label, state FIPS, [exact lowercase city strings])
COMPARE_CITIES = [
    ("Los Angeles", 6, ["los angeles"]),
    ("Chicago", 17, ["chicago"]),
    ("Houston", 48, ["houston"]),
    ("Phoenix", 4, ["phoenix"]),
    ("Philadelphia", 42, ["philadelphia"]),
    ("San Antonio", 48, ["san antonio"]),
    ("San Diego", 6, ["san diego"]),
    ("Boston", 25, ["boston"]),
    ("Washington, D.C.", 11, ["washington"]),
    ("San Francisco", 6, ["san francisco"]),
    ("Atlanta", 13, ["atlanta"]),
    ("Seattle", 53, ["seattle"]),
]


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


def fetch_degree_fields(nyc_unitids):
    """Citywide graduate/professional degrees conferred, grouped by CIP field.
    Source: IPEDS completions (degrees awarded), not enrollment."""
    fields = {}
    total = 0
    for lvl in GRAD_AWARD_LEVELS:
        url = (f"{BASE}/completions-cip-2/{DEGREE_YEAR}/"
               f"?fips=36&race=99&sex=99&majornum=1&award_level={lvl}")
        for r in get(url)["results"]:
            if r["unitid"] not in nyc_unitids:
                continue
            cip = r.get("cipcode")
            awards = r.get("awards") or 0
            if awards <= 0 or cip in (None, 990000, 99):
                continue  # skip the all-fields total row
            family = int(cip // 10000)
            label = CIP_FAMILY.get(family, "Other fields")
            fields[label] = fields.get(label, 0) + awards
            total += awards
    rows = sorted(({"field": k, "awards": v} for k, v in fields.items()),
                  key=lambda x: x["awards"], reverse=True)
    print(f"degree fields: {len(rows)} fields, {total:,} graduate/professional degrees ({DEGREE_YEAR})")
    return {"year": DEGREE_YEAR, "total": total, "fields": rows}


def fetch_city_comparison(nyc_ug, nyc_gr, nyc_excl, nyc_online_tot, dist):
    """Undergraduate and graduate/professional enrollment for major U.S. cities
    (city-proper, IPEDS fall enrollment), to compare against New York City.
    Each city also gets its share of students enrolled exclusively online."""
    state_dir, state_enr = {}, {}

    def dir_for(st):
        if st not in state_dir:
            rows = get_all(f"{BASE}/directory/{YEAR}/?fips={st}")
            state_dir[st] = {r["unitid"]: (r.get("city") or "").strip().lower() for r in rows}
        return state_dir[st]

    def enr_for(st, lvl):
        key = (st, lvl)
        if key not in state_enr:
            rows = get_all(f"{BASE}/fall-enrollment/{YEAR}/{lvl}/race/sex/"
                           f"?fips={st}&race=99&sex=99&ftpt=99&class_level=99&degree_seeking=99")
            state_enr[key] = {r["unitid"]: (r.get("enrollment_fall") or 0)
                              for r in rows if (r.get("enrollment_fall") or 0) > 0}
        return state_enr[key]

    def online_pct(excl, tot):
        return round(excl / tot * 100, 1) if tot else 0

    cities = [{"city": "New York", "undergrad": nyc_ug, "grad": nyc_gr,
               "total": nyc_ug + nyc_gr, "online_excl": nyc_excl,
               "online_pct": online_pct(nyc_excl, nyc_online_tot), "nyc": True}]
    for label, st, matches in COMPARE_CITIES:
        d = dir_for(st)
        units = {u for u, c in d.items() if c in matches}
        ug_map, gr_map = enr_for(st, 1), enr_for(st, 2)
        ug = sum(ug_map.get(u, 0) for u in units)
        gr = sum(gr_map.get(u, 0) for u in units)
        oa = online_agg(dist, units, 1)
        cities.append({"city": label, "undergrad": ug, "grad": gr, "total": ug + gr,
                       "online_excl": oa["exc"], "online_pct": online_pct(oa["exc"], oa["tot"])})
        print(f"  {label}: ug={ug:,} gr={gr:,}  fully online={online_pct(oa['exc'], oa['tot'])}%")
    cities.sort(key=lambda c: c["total"], reverse=True)
    return {"year": YEAR, "cities": cities}


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

    print("Fetching graduate / professional degrees by field...")
    degree_fields = fetch_degree_fields(set(directory.keys()))

    print("Fetching distance-education (online) data from NCES...")
    dist = load_distance_education()

    # attach each campus's online mix (so the map can drop fully-online students)
    for c in campuses:
        d = dist.get(c["unitid"], {})
        d2, d12 = d.get(2, {}), d.get(12, {})
        c["o_ug_exc"], c["o_ug_som"], c["o_ug_non"] = d2.get("exc", 0), d2.get("som", 0), d2.get("non", 0)
        c["o_gr_exc"], c["o_gr_som"], c["o_gr_non"] = d12.get("exc", 0), d12.get("som", 0), d12.get("non", 0)

    nyc_units = set(directory.keys())
    online = {
        "year": YEAR,
        "all": online_agg(dist, nyc_units, 1),
        "undergrad": online_agg(dist, nyc_units, 2),
        "grad": online_agg(dist, nyc_units, 12),
    }
    oa = online["all"]
    print(f"  NYC online: {oa['exc']:,} fully online of {oa['tot']:,} "
          f"({round(oa['exc']/oa['tot']*100,1)}%); some online {oa['som']:,}")

    tot_ug0 = sum(c["undergrad"] for c in campuses)
    tot_gr0 = sum(c["grad"] for c in campuses)
    print("Fetching city comparison (other U.S. cities)...")
    city_comparison = fetch_city_comparison(tot_ug0, tot_gr0, oa["exc"], oa["tot"], dist)

    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "year": YEAR,
        "source": "Urban Institute Education Data Portal (IPEDS fall enrollment)",
        "count": len(campuses),
        "campuses": campuses,
        "degree_fields": degree_fields,
        "online": online,
        "city_comparison": city_comparison,
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

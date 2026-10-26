"""Slim the GRID3 Nigeria health-facility register into the two CSVs the app reads.

Usage:
    python scripts/build_facilities.py            # download from HDX, write full + test CSVs
    python scripts/build_facilities.py raw.csv    # use an already-downloaded GRID3 CSV

Source: GRID3 NGA Health Facilities, Humanitarian Data Exchange
https://data.humdata.org/dataset/nigeria-health-care-facilities-in-nigeria
"""
import csv
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRID3_URL = (
    "https://data.humdata.org/dataset/bfd0888d-ca7f-40ec-b1e7-97eb3fe15dc2/resource/"
    "1ad3ca9f-d8f0-4c13-8e57-246c2f834c4e/download/grid3_nga_-_health_facilities_-1.csv"
)
TEST_STATES = {"Lagos", "Kano", "Federal Capital Territory", "Anambra", "Enugu", "Imo", "Abia", "Ebonyi"}
STATE_NAMES = {"Fct": "Federal Capital Territory"}
FIELDS = ["name", "level", "category", "ownership", "functional", "ward", "lga", "state", "lat", "lon"]


def slim(row: dict) -> dict | None:
    if row["func_stats"] == "Not Functional" or row["category"] == "Laboratory":
        return None
    try:
        lat, lon = round(float(row["latitude"]), 5), round(float(row["longitude"]), 5)
    except ValueError:
        return None
    return {
        "name": row["prmry_name"].strip(),
        "level": row["type"].strip(),
        "category": row["category"].strip(),
        "ownership": row["ownership"].strip(),
        "functional": row["func_stats"].strip(),
        "ward": row["wardname"].strip(),
        "lga": row["lganame"].strip(),
        "state": STATE_NAMES.get(row["statename"].strip(), row["statename"].strip()),
        "lat": lat,
        "lon": lon,
    }


def main() -> None:
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1])
    else:
        raw = ROOT / "data" / "grid3_raw.csv"
        print(f"Downloading GRID3 register to {raw} ...")
        urllib.request.urlretrieve(GRID3_URL, raw)
    with open(raw, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in (slim(r) for r in csv.DictReader(f)) if r and r["name"]]
    for path, subset in [
        (ROOT / "data" / "facilities_full.csv", rows),
        (ROOT / "data" / "facilities_test.csv", [r for r in rows if r["state"] in TEST_STATES]),
    ]:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(subset)
        print(f"Wrote {len(subset):,} facilities to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

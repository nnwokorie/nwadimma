"""Nearest-facility lookup over the GRID3 Nigeria health-facility register."""
import csv
import difflib
import math
import re
from collections import defaultdict
from functools import cache

from . import config

HOSPITAL_LEVELS = {"Secondary", "Tertiary"}
# Hospitals that cannot take a sick child (orthopaedic, psychiatric, eye, dental, ...).
NOT_FOR_SICK_CHILD = re.compile(
    r"orthop|psych|urolog|neuro|eye|ophthalm|dental|ear,? nose|e\.?n\.?t\b|cancer|onco|skin|dermat|leprosy|"
    r"tubercul|\btb\b|rehab|mental|prison|fertility|cosmetic|diagnostic|laborator|imaging|staff clinic", re.I)
PRIVATE = re.compile(r"\bprivate\b|\bothers\b|\bbank\b", re.I)


@cache
def facilities() -> list[dict]:
    with open(config.FACILITY_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
    return rows


@cache
def lga_index() -> dict[tuple[str, str], tuple[float, float]]:
    """(state, lga) -> centroid of that LGA's facilities, used when the CHW gives no GPS."""
    pts = defaultdict(list)
    for r in facilities():
        pts[(r["state"], r["lga"])].append((r["lat"], r["lon"]))
    return {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)) for k, v in pts.items()}


def states_and_lgas() -> dict[str, list[str]]:
    out = defaultdict(list)
    for state, lga in sorted(lga_index()):
        out[state].append(lga)
    return dict(out)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def resolve_lga(state: str, lga: str) -> tuple[str, str] | None:
    keys = list(lga_index())
    if state.strip().lower() in ("fct", "abuja", "fct abuja", "fct-abuja"):
        state = "Federal Capital Territory"
    state_match = difflib.get_close_matches(state.title(), sorted({s for s, _ in keys}), n=1, cutoff=0.6)
    if not state_match:
        return None
    lgas = [l for s, l in keys if s == state_match[0]]
    lga_match = difflib.get_close_matches(lga.title(), lgas, n=1, cutoff=0.6)
    return (state_match[0], lga_match[0]) if lga_match else None


def nearest(state: str = "", lga: str = "", lat: float | None = None, lon: float | None = None, n: int = 2) -> dict:
    """Closest primary facilities and closest hospitals to a GPS point or an LGA centre."""
    if lat is not None and lon is not None:
        origin, where = (lat, lon), f"GPS {lat:.4f}, {lon:.4f}"
    else:
        match = resolve_lga(state, lga)
        if not match:
            return {"error": f"Location '{lga}, {state}' not found in the {config.DATA_MODE} facility register."}
        origin, where = lga_index()[match], f"centre of {match[1]} LGA, {match[0]} State"

    def pick(pred):
        # Public facilities first (they take every child and follow national protocols); private only as fallback.
        pool = [r for r in facilities() if pred(r) and not PRIVATE.search(r["ownership"] + " " + r["name"])]
        pool = pool or [r for r in facilities() if pred(r)]
        ranked = sorted(pool, key=lambda r: km(origin, (r["lat"], r["lon"])))
        return [
            {k: r[k] for k in ("name", "category", "level", "ownership", "functional", "ward", "lga", "state")}
            | {"distance_km": round(km(origin, (r["lat"], r["lon"])), 1),
               "map": f"https://www.google.com/maps?q={r['lat']},{r['lon']}"}
            for r in ranked[:n]
        ]

    return {
        "searched_from": where,
        "primary_health_centres": pick(lambda r: r["level"] not in HOSPITAL_LEVELS and r["category"] == "Primary Health Center" and not NOT_FOR_SICK_CHILD.search(r["name"])),
        "hospitals": pick(lambda r: r["level"] in HOSPITAL_LEVELS and not NOT_FOR_SICK_CHILD.search(r["name"])),
        "source": "GRID3 Nigeria Health Facilities register (HDX)",
    }

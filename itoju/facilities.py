"""Nearest-facility lookup over the GRID3 Nigeria health-facility register."""
import csv
import difflib
import math
import re
from collections import defaultdict
from functools import cache

from . import config

HOSPITAL_LEVELS = {"Secondary", "Tertiary"}
# Facilities that cannot manage a birth emergency (orthopaedic, psychiatric, eye, dental, labs, ...).
NOT_SUITABLE = re.compile(
    r"orthop|psych|urolog|neuro|eye|ophthalm|dental|ear,? nose|e\.?n\.?t\b|cancer|onco|skin|dermat|leprosy|"
    r"tubercul|\btb\b|rehab|mental|prison|fertility|cosmetic|diagnostic|laborator|imaging|staff clinic|x-? ?ray|scan|school of", re.I)
# Proxy for comprehensive emergency obstetric care (caesarean + blood transfusion): GRID3 has no EmONC flag,
# so prefer general, teaching and federal hospitals over small private "specialist" clinics when within reach.
FULL_HOSPITAL = re.compile(r"general hospital|teaching hospital|federal medical|medical cent(er|re)|state hospital|specialist hospital|comprehensive health|cottage hospital|university of|women|maternity", re.I)
NOT_FOR_BIRTH = re.compile(r"paediatric|pediatric|children|infectious disease|military|police|barracks", re.I)
BIRTH_CATEGORIES = {"Primary Health Center", "Maternity Home", "Comprehensive Health Center"}
PREFERRED_KM = 25
PRIVATE = re.compile(r"\bprivate\b|\bbank\b", re.I)


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


def resolve_state(state: str) -> str | None:
    if state.strip().lower() in ("fct", "abuja", "fct abuja", "fct-abuja"):
        state = "Federal Capital Territory"
    match = difflib.get_close_matches(state.title(), sorted({s for s, _ in lga_index()}), n=1, cutoff=0.6)
    return match[0] if match else None


def resolve_lga(state: str, lga: str) -> tuple[str, str] | None:
    state_match = resolve_state(state)
    if not state_match or not lga:
        return None
    lgas = [l for s, l in lga_index() if s == state_match]
    lga_match = difflib.get_close_matches(lga.title(), lgas, n=1, cutoff=0.6)
    return (state_match, lga_match[0]) if lga_match else None


def state_centre(state: str) -> tuple[float, float]:
    pts = [r for r in facilities() if r["state"] == state]
    return sum(r["lat"] for r in pts) / len(pts), sum(r["lon"] for r in pts) / len(pts)


def nearest(state: str = "", lga: str = "", lat: float | None = None, lon: float | None = None, n: int = 2) -> dict:
    """Closest hospitals (caesarean-capable proxy) and closest birth facilities (PHC / maternity) to a point or LGA."""
    if lat is not None and lon is not None:
        origin, where = (lat, lon), f"GPS {lat:.4f}, {lon:.4f}"
    else:
        match = resolve_lga(state, lga)
        if match:
            origin, where = lga_index()[match], f"centre of {match[1]} LGA, {match[0]} State"
        elif (st := resolve_state(state)):  # state chosen but no (or unknown) LGA: search from the middle of the state
            origin, where = state_centre(st), f"centre of {st} State (no LGA given)"
        else:
            return {"error": f"Location '{', '.join(x for x in (lga, state) if x) or 'none'}' not found in the "
                             f"{config.DATA_MODE} facility register. Choose a state, or go to the nearest general hospital."}

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

    def pick_hospitals():
        ok = lambda r: (r["level"] in HOSPITAL_LEVELS and r["category"] != "Educational Clinic"
                        and not NOT_SUITABLE.search(r["name"]) and not NOT_FOR_BIRTH.search(r["name"]))
        general = re.compile(r"general hospital|teaching hospital|federal medical|medical cent(er|re)|state hospital|comprehensive", re.I)
        full = [h for h in pick(lambda r: ok(r) and general.search(r["name"] + " " + r["category"]))
                if h["distance_km"] <= PREFERRED_KM]
        return full or pick(ok)

    return {
        "searched_from": where,
        "birth_facilities": pick(lambda r: r["category"] in BIRTH_CATEGORIES and not NOT_SUITABLE.search(r["name"])),
        "hospitals": pick_hospitals(),
        "source": "GRID3 Nigeria Health Facilities register (HDX)",
    }

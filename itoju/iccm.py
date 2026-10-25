"""WHO/UNICEF iCCM sick-child protocol as plain code.

The LLM agents never decide a classification or a dose on their own: they call these
functions as tools, and the safety reviewer checks their final text against these outputs.
Reference: WHO/UNICEF, "Caring for the Sick Child in the Community" (2011), the protocol
Nigeria's community health workers (CHEWs) are trained on for children 2 months up to 5 years.
"""
import json
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field

from .config import ROOT

YesNo = bool | None  # None = the CHW did not say; never treated as "no" for a danger sign


class Findings(BaseModel):
    """Structured case findings. Unknown values stay None."""

    age_months: int | None = Field(None, ge=0, le=216)
    sex: Literal["male", "female"] | None = None
    cough: YesNo = None
    cough_days: int | None = Field(None, ge=0)
    breaths_per_minute: int | None = Field(None, ge=5, le=150)
    chest_indrawing: YesNo = None
    diarrhoea: YesNo = None
    diarrhoea_days: int | None = Field(None, ge=0)
    blood_in_stool: YesNo = None
    fever: YesNo = None
    fever_days: int | None = Field(None, ge=0)
    rdt_result: Literal["positive", "negative", "not_done"] | None = None
    convulsions: YesNo = None
    unable_to_drink_or_feed: YesNo = None
    vomits_everything: YesNo = None
    unusually_sleepy_or_unconscious: YesNo = None
    swelling_both_feet: YesNo = None
    muac_color: Literal["red", "yellow", "green"] | None = None
    other_concerns: list[str] = Field(default_factory=list)


DANGER_SIGN_LABELS = {
    "convulsions": "Convulsions",
    "unable_to_drink_or_feed": "Not able to drink or feed anything",
    "vomits_everything": "Vomits everything",
    "unusually_sleepy_or_unconscious": "Unusually sleepy or unconscious",
    "chest_indrawing": "Chest indrawing",
    "swelling_both_feet": "Swelling of both feet",
    "blood_in_stool": "Blood in stool",
}


@cache
def dosing_table() -> dict:
    return json.loads((ROOT / "data" / "iccm_dosing.json").read_text(encoding="utf-8"))


def get_dose(drug: str, age_months: int) -> dict:
    """Exact iCCM dose for a drug and age. Returns an error instead of guessing out of range."""
    table = dosing_table()["drugs"]
    if drug not in table:
        return {"error": f"Unknown drug '{drug}'. Allowed: {sorted(table)}"}
    spec = table[drug]
    for band in spec["bands"]:
        if band["min_months"] <= age_months < band["max_months"]:
            return {"drug": drug, "label": spec["label"], "dose": band["dose"], "how": spec["how"],
                    "age_band": f"{band['min_months']}-{band['max_months']} months"}
    return {"error": f"No community dose for {drug} at {age_months} months. Refer to a health facility."}


def fast_breathing_cutoff(age_months: int) -> int:
    return 50 if age_months < 12 else 40


def danger_signs(f: Findings) -> list[str]:
    signs = [label for key, label in DANGER_SIGN_LABELS.items() if getattr(f, key) is True]
    if f.cough and (f.cough_days or 0) >= 14:
        signs.append("Cough for 14 days or more")
    if f.diarrhoea and (f.diarrhoea_days or 0) >= 14:
        signs.append("Diarrhoea for 14 days or more")
    if f.fever and (f.fever_days or 0) >= 7:
        signs.append("Fever for 7 days or more")
    if f.muac_color == "red" and (f.age_months or 0) >= 6:
        signs.append("Red on MUAC strap (severe malnutrition)")
    return signs


def classify(f: Findings) -> dict:
    """Apply iCCM. Returns the urgency, conditions, treatments with exact doses, and any missing info."""
    missing: list[str] = []
    conditions: list[str] = []
    treatments: list[dict] = []
    rules: list[str] = []

    if f.age_months is None:
        missing.append("Child's age in months (needed for every dose)")
    elif f.age_months < 2:
        return _result("refer_urgently", ["Young infant under 2 months — outside community treatment"],
                       [], [], ["iCCM: community treatment covers 2 months up to 5 years; refer all young infants"], [])
    elif f.age_months >= 60:
        return _result("refer_urgently", ["Child is 5 years or older — outside the iCCM protocol"],
                       [], [], ["iCCM covers children 2 months up to 5 years"], [])

    signs = danger_signs(f)
    age = f.age_months

    fast = None
    if f.cough:
        if f.breaths_per_minute is None:
            missing.append("Breaths in one full minute (child calm) — needed to check for pneumonia")
        elif age is not None:
            fast = f.breaths_per_minute >= fast_breathing_cutoff(age)

    if f.fever and not signs and f.rdt_result in (None, "not_done"):
        missing.append("Malaria RDT result — do the RDT before giving any antimalarial")

    if signs:
        conditions = [f"Danger sign: {s}" for s in signs]
        rules.append("iCCM: ANY danger sign -> refer URGENTLY to health facility after pre-referral treatment")
        if age is not None:
            can_drink = not (f.unable_to_drink_or_feed or f.vomits_everything)
            if f.diarrhoea and can_drink:
                treatments.append({**get_dose("ors", age), "note": "Begin right away; keep giving on the way"})
            if f.fever:
                if not can_drink or f.convulsions or f.unusually_sleepy_or_unconscious:
                    treatments.append({**get_dose("rectal_artesunate", age), "note": "Pre-referral dose"})
                elif f.rdt_result == "positive":
                    treatments.append({**get_dose("artemether_lumefantrine", age), "note": "First dose only, then refer"})
            if (f.chest_indrawing or fast) and can_drink:
                treatments.append({**get_dose("amoxicillin", age), "note": "First dose only, then refer"})
        return _result("refer_urgently", conditions, treatments, [], rules, [])

    if missing:
        return _result("needs_info", [], [], [], ["Missing information blocks a safe classification"], missing)

    if f.diarrhoea:
        conditions.append("Diarrhoea (less than 14 days, no blood)")
        treatments += [get_dose("ors", age), get_dose("zinc", age)]
        rules.append("iCCM: diarrhoea <14 days, no blood -> ORS + zinc at home")
    if f.fever:
        if f.rdt_result == "positive":
            conditions.append("Malaria (fever less than 7 days, RDT positive)")
            treatments.append(get_dose("artemether_lumefantrine", age))
            rules.append("iCCM: fever <7 days + RDT positive -> oral AL")
        else:
            conditions.append("Fever, RDT negative — not malaria")
            rules.append("iCCM: RDT negative -> no antimalarial; advise caregiver, follow up in 3 days")
    if f.cough and fast:
        conditions.append(f"Pneumonia — fast breathing ({f.breaths_per_minute}/min, cutoff {fast_breathing_cutoff(age)})")
        treatments.append(get_dose("amoxicillin", age))
        rules.append("iCCM: cough + fast breathing -> oral amoxicillin")
    elif f.cough:
        conditions.append("Cough or cold, no fast breathing")
        rules.append("iCCM: cough without fast breathing -> home care, soothing remedy")
    if f.muac_color == "yellow":
        conditions.append("Yellow on MUAC — moderate malnutrition")
        rules.append("iCCM: yellow MUAC -> counsel on feeding, refer to nutrition programme if available")

    if not conditions:
        conditions.append("No iCCM condition found")
        return _result("home_care", conditions, [], ["Return immediately if any danger sign appears"], rules, [])

    urgency = "treat_at_home" if treatments else "home_care"
    follow_up = ["Follow up in 3 days", "Return immediately if the child cannot drink, gets worse, or develops any danger sign"]
    return _result(urgency, conditions, treatments, follow_up, rules, [])


def _result(urgency, conditions, treatments, follow_up, rules, missing) -> dict:
    return {
        "urgency": urgency,
        "conditions": conditions,
        "treatments": treatments,
        "follow_up": follow_up,
        "rules_applied": rules,
        "missing_info": missing,
    }

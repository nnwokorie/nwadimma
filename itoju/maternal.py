"""WHO pregnancy, childbirth and postpartum danger signs as plain code.

The LLM agents never decide the urgency, where to go, or a medicine on their own: they call
these functions as tools, and the output check compares their text against what this returns.
References: WHO PCPNC (2015) danger signs and rapid assessment; WHO MCPC (2017) pre-referral
management; WHO PPH recommendations (2012).
"""
import json
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field

from .config import ROOT

YesNo = bool | None  # None = not mentioned; never treated as "no" for a danger sign
Stage = Literal["pregnant", "in_labour", "after_birth"]


class Findings(BaseModel):
    """Structured findings about one woman. Unknown values stay None."""

    stage: Stage | None = None
    gestation_weeks: int | None = Field(None, ge=1, le=45)
    mother_age: int | None = Field(None, ge=9, le=60)
    hours_in_labour: float | None = Field(None, ge=0, le=200)
    hours_since_waters_broke: float | None = Field(None, ge=0, le=500)
    hours_since_birth: float | None = Field(None, ge=0, le=1100)
    labour_pains: YesNo = None
    waters_broken: YesNo = None
    placenta_delivered: YesNo = None
    vaginal_bleeding: YesNo = None
    heavy_bleeding: YesNo = None
    convulsions: YesNo = None
    unconscious_or_very_drowsy: YesNo = None
    severe_headache: YesNo = None
    blurred_vision: YesNo = None
    swelling_face_or_hands: YesNo = None
    bp_systolic: int | None = Field(None, ge=50, le=300)
    bp_diastolic: int | None = Field(None, ge=20, le=200)
    fever: YesNo = None
    very_weak: YesNo = None
    severe_abdominal_pain: YesNo = None
    difficulty_breathing: YesNo = None
    reduced_or_no_fetal_movement: YesNo = None
    cord_or_limb_visible: YesNo = None
    foul_or_green_fluid: YesNo = None
    previous_caesarean: YesNo = None
    multiple_pregnancy: YesNo = None
    baby_not_head_down: YesNo = None
    previous_heavy_bleeding: YesNo = None
    other_concerns: list[str] = Field(default_factory=list)


# Fields the safety net may switch on (escalation only).
DANGER_FIELDS = {
    "vaginal_bleeding": "Vaginal bleeding",
    "heavy_bleeding": "Heavy bleeding",
    "convulsions": "Convulsions (fits)",
    "unconscious_or_very_drowsy": "Unconscious or very drowsy",
    "severe_headache": "Severe headache",
    "blurred_vision": "Blurred vision",
    "difficulty_breathing": "Difficulty breathing",
    "severe_abdominal_pain": "Severe abdominal pain",
    "reduced_or_no_fetal_movement": "Baby not moving / moving much less",
    "cord_or_limb_visible": "Cord, hand or foot visible",
    "foul_or_green_fluid": "Foul-smelling or green fluid",
    "fever": "Fever",
    "very_weak": "Very weak",
}
RISK_FACTORS = {
    "previous_caesarean": "Previous caesarean section",
    "multiple_pregnancy": "Twins or more",
    "baby_not_head_down": "Baby not head-down (breech or transverse)",
    "previous_heavy_bleeding": "Heavy bleeding after a previous birth",
}

URGENCY_ORDER = ["plan_ahead", "facility_today", "hospital_now", "emergency"]
HEADLINES = {
    "emergency": "Go now to a hospital that can handle obstetric emergencies (surgery, blood, intensive care). Do not wait.",
    "hospital_now": "Go now to a hospital that can do a caesarean section — she needs hospital-level care for this birth.",
    "facility_today": "Go to a health facility with a skilled birth attendant today.",
    "plan_ahead": "No danger signs now. Make a birth plan and know the danger signs.",
    "needs_info": "A few answers are needed before a safe decision.",
}
EMERGENCY_ACTIONS = [
    "Arrange transport now — do not wait for morning or for the husband's permission",
    "Bring 2 relatives who can donate blood, the antenatal card, and money for transport",
    "Keep her lying on her LEFT side and keep her warm; someone stays with her the whole way",
    "Call or send word to the hospital that you are coming",
]


@cache
def protocol() -> dict:
    return json.loads((ROOT / "data" / "maternal_protocol.json").read_text(encoding="utf-8"))


def get_drug(drug: str) -> dict:
    """Exact WHO pre-referral dose for one drug. Returns an error rather than guessing."""
    table = protocol()["drugs"]
    if drug not in table:
        return {"error": f"Unknown drug '{drug}'. Allowed: {sorted(table)}"}
    spec = table[drug]
    return {"drug": drug, "label": spec["label"], "dose": spec["dose"], "how": spec["how"],
            "note": "Only if a trained health worker and the medicine are available. Never delay transport for it."}


def infer_stage(f: Findings) -> Stage | None:
    if f.stage:
        return f.stage
    if f.hours_since_birth is not None or f.placenta_delivered is not None:
        return "after_birth"
    if f.labour_pains or f.hours_in_labour is not None:
        return "in_labour"
    if f.gestation_weeks is not None or f.reduced_or_no_fetal_movement is not None:
        return "pregnant"
    return None


def classify(f: Findings) -> dict:
    """Apply the WHO danger-sign rules. Returns urgency, destination, actions, medicines and reasons."""
    stage = infer_stage(f)
    level = "plan_ahead"
    conditions: list[str] = []
    actions: list[str] = []
    drugs: list[str] = []
    rules: list[str] = []

    def raise_to(new: str, condition: str, rule: str):
        nonlocal level
        if URGENCY_ORDER.index(new) > URGENCY_ORDER.index(level):
            level = new
        conditions.append(condition)
        rules.append(rule)

    sbp, dbp = f.bp_systolic or 0, f.bp_diastolic or 0
    severe_bp = sbp >= 160 or dbp >= 110
    high_bp = sbp >= 140 or dbp >= 90

    # ---- Emergency danger signs (WHO PCPNC rapid assessment) ----
    if f.convulsions or f.unconscious_or_very_drowsy:
        raise_to("emergency", "Convulsions or unconscious — possible eclampsia", "PCPNC: convulsions/unconscious -> emergency referral")
        actions.append("Protect her from injury, turn her on her left side, put nothing in her mouth")
        drugs.append("magnesium_sulfate")
    if f.severe_headache or f.blurred_vision or severe_bp or (high_bp and f.swelling_face_or_hands):
        raise_to("emergency", "Signs of severe pre-eclampsia (severe headache, blurred vision or very high blood pressure)",
                 "PCPNC: severe headache / blurred vision / BP >=160/110 -> emergency referral")
        drugs.append("magnesium_sulfate")
    if f.vaginal_bleeding and not (stage == "in_labour" and f.heavy_bleeding is False):
        label = {"after_birth": "Heavy bleeding after birth (postpartum haemorrhage)",
                 "in_labour": "Bleeding in labour (more than a show)"}.get(stage or "", "Bleeding in pregnancy")
        raise_to("emergency", label, "PCPNC: vaginal bleeding in pregnancy, heavy bleeding in labour or after birth -> emergency")
        if stage == "after_birth":
            actions += ["Rub the womb firmly through the belly until it is hard, and keep rubbing",
                        "Put the baby to the breast and empty her bladder — both help the womb contract"]
            drugs += ["oxytocin", "misoprostol"]
    if stage == "after_birth" and f.placenta_delivered is False and (f.hours_since_birth is None or f.hours_since_birth >= 0.5):
        raise_to("emergency", "Placenta not delivered 30 minutes after birth",
                 "PCPNC: placenta not delivered within 30 minutes -> emergency")
        actions.append("Do not pull on the cord; put the baby to the breast")
        drugs.append("oxytocin")
    if f.cord_or_limb_visible:
        raise_to("emergency", "Cord, hand or foot visible before the baby", "PCPNC: cord prolapse / abnormal presentation -> emergency")
        actions.append("Raise her hips (knees to chest) and do not push the cord back")
    if f.hours_in_labour is not None and f.hours_in_labour >= 12:
        raise_to("emergency", f"Labour for {f.hours_in_labour:g} hours — prolonged labour",
                 "PCPNC: labour longer than 12 hours -> emergency referral")
    if f.reduced_or_no_fetal_movement and stage != "after_birth":
        raise_to("emergency", "Baby not moving or moving much less", "PCPNC: no fetal movement -> urgent referral")
    if f.waters_broken and ((f.hours_since_waters_broke or 0) >= 18 or f.foul_or_green_fluid):
        raise_to("emergency", "Waters broken 18+ hours or foul/green fluid — infection risk",
                 "PCPNC: rupture of membranes >18 h or foul/meconium fluid -> referral")
    elif f.foul_or_green_fluid:
        raise_to("emergency", "Foul-smelling or green fluid", "PCPNC: foul discharge / meconium -> referral")
    if f.fever and (f.very_weak or f.foul_or_green_fluid or stage == "after_birth"):
        raise_to("emergency", "Fever with weakness or after birth — possible serious infection",
                 "PCPNC: fever + very weak / postpartum fever -> emergency")
    if f.severe_abdominal_pain:
        raise_to("emergency", "Severe abdominal pain (not normal labour pains)", "PCPNC: severe abdominal pain -> emergency")
    if f.difficulty_breathing:
        raise_to("emergency", "Difficulty breathing", "PCPNC: difficult breathing -> emergency")

    # ---- Needs hospital-level care, not yet an emergency ----
    risks = [label for key, label in RISK_FACTORS.items() if getattr(f, key)]
    if f.mother_age is not None and f.mother_age < 16:
        risks.append(f"Very young mother ({f.mother_age})")
    preterm = f.gestation_weeks is not None and f.gestation_weeks < 37
    if stage == "in_labour" and (risks or preterm):
        why = risks + (["Labour before 37 weeks"] if preterm else [])
        raise_to("hospital_now", "In labour with a risk factor: " + "; ".join(why),
                 "PCPNC: labour + risk factor (previous caesarean, twins, breech, preterm, very young) -> hospital birth")

    # ---- Needs a facility today ----
    if stage == "in_labour":
        raise_to("facility_today", "In labour", "WHO: every birth with a skilled birth attendant in a health facility")
    if f.waters_broken and stage != "after_birth":
        raise_to("facility_today", "Waters broken", "PCPNC: ruptured membranes -> facility assessment")
    if high_bp:
        raise_to("facility_today", f"High blood pressure ({sbp}/{dbp})", "PCPNC: BP >=140/90 -> same-day assessment")
    elif f.swelling_face_or_hands:
        raise_to("facility_today", "Swelling of face or hands", "PCPNC: facial/hand oedema -> check blood pressure today")
    if f.fever:
        raise_to("facility_today", "Fever", "PCPNC: fever in pregnancy -> same-day assessment")
    if f.very_weak:
        raise_to("facility_today", "Very weak or tired", "PCPNC: severe weakness -> check for anaemia today")

    # ---- Not enough information to decide safely ----
    known = [k for k in DANGER_FIELDS if getattr(f, k) is not None]
    vague = level == "plan_ahead" and not known and not risks and f.bp_systolic is None
    if stage is None and level == "plan_ahead":
        return _result("needs_info", [], [], [], [], ["No danger sign found yet, but the situation is unclear"],
                       ["Is she pregnant, in labour now, or did she give birth recently?",
                        "Any bleeding, fits, severe headache or blurred vision, fever, or trouble breathing?"], None)
    if vague:
        questions = {
            "pregnant": ["Any bleeding from the vagina?", "Severe headache or blurred vision?",
                         "Is the baby moving as usual?", "Fever, or very weak?"],
            "in_labour": ["How many hours since regular pains started?", "Any bleeding (more than a little show)?",
                          "Have the waters broken? How long ago, and is the fluid clear?"],
            "after_birth": ["How heavy is the bleeding (how many pads or wrappers soaked)?", "Has the placenta come out?",
                            "Fever, fits, or severe headache?"],
        }[stage]
        return _result("needs_info", [], [], [], [], ["No danger sign confirmed or ruled out yet"], questions, None)

    # ---- Build the result ----
    if level in ("emergency", "hospital_now"):
        destination = "emergency_hospital"
        actions = list(dict.fromkeys(actions)) + EMERGENCY_ACTIONS
    elif level == "facility_today":
        destination = "birth_facility" if stage == "in_labour" or f.waters_broken else "health_facility"
        actions = ["Go to the facility today with the antenatal card and a companion"]
        if destination == "birth_facility":
            actions.append("Bring the birth kit: clean wrappers, pads, baby clothes, soap")
    else:
        destination = None
        conditions = conditions or ["No danger signs found"]
        plan = ["Plan to give birth in a health facility with a skilled birth attendant",
                "Save money for transport and agree now who will drive, day or night",
                "Know the danger signs: bleeding, fits, severe headache or blurred vision, fever, baby not moving, "
                "waters breaking — go at once if any appear"]
        if risks:
            conditions.append("Risk factor: " + "; ".join(risks))
            plan[0] = "Plan to give birth in a HOSPITAL that can do a caesarean section (risk factor present)"
        if stage == "after_birth":
            plan = ["Postnatal check within 48 hours, then at 1 and 6 weeks",
                    "Go at once for heavy bleeding, fever, fits, severe headache or trouble breathing"]
        actions = plan

    treatments = [get_drug(d) for d in dict.fromkeys(drugs)] if level == "emergency" else []
    follow_up = ["If any new danger sign appears on the way, go to the nearest hospital instead"] if destination else []
    return _result(level, conditions, list(dict.fromkeys(actions)), treatments, follow_up, rules, [], destination)


def _result(urgency, conditions, actions, treatments, follow_up, rules, missing, destination) -> dict:
    return {
        "urgency": urgency,
        "headline": HEADLINES[urgency],
        "conditions": conditions,
        "actions": actions,
        "treatments": treatments,
        "destination": destination,
        "follow_up": follow_up,
        "rules_applied": rules,
        "missing_info": missing,
    }

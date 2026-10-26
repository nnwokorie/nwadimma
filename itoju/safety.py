"""Deterministic guardrails that run around the LLM agents.

1. screen_input      — blocks prompt-injection attempts and oversized notes before any model call.
2. danger_word_scan  — English/Pidgin/Igbo keyword net for pregnancy danger signs; anything it finds
                       that the intake agent missed is forced on (it can only make care MORE urgent).
3. verify_messages   — every medicine name and dose in the agents' free text must come from the
                       protocol engine's output; otherwise the text is replaced with a safe template.
"""
import re
import unicodedata

from . import config

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (the |all |your )?(rules|instructions|protocol|guidelines)",
    r"(reveal|show|print|repeat) (me )?(your |the )?(system prompt|instructions|api key|prompt)",
    r"\byou are now\b|\bact as\b|\bpretend to be\b|\bdeveloper mode\b|\bjailbreak\b",
    r"\b(api[_ ]?key|portkey|openai_api_key)\b",
    r"new instructions?:|system:|<\s*/?\s*system\s*>",
]

# Matched after lowercasing and stripping tone marks, so "ọbara" and "obara" both match.
DANGER_WORDS = {
    "vaginal_bleeding": [r"\bbleed", r"blood (dey |is |was )?(commot|comot|come out|coming|flow|pour)",
                         r"(passing|losing|lost|see|saw|seen|notice\w*) (small |some |a lot of |plenty )?blood",
                         r"blood (for|in|on) (her )?(pant|cloth|wrapper|pad)",
                         r"obara (na-?)?(agba|aputa|asoputa)", r"na-?agba obara"],
    "heavy_bleeding": [r"(heavy|plenty|a lot of|lots of|too much) (blood|bleeding)",
                       r"blood (dey |is )?(commot|comot|come out|coming|flow)\w* (plenty|well well|too much|a lot)",
                       r"soak\w* .{0,25}(wrapper|pad|cloth|bed)", r"obara (na-?)?agba (nke ukwuu|ike|oke)"],
    "convulsions": [r"convuls", r"seizure", r"\bfits?\b", r"\bjerk", r"eyes? (roll|turn)", r"anya ya na-?atughari"],
    "unconscious_or_very_drowsy": [r"unconscious", r"won'?t wake", r"hard to wake", r"no dey wake", r"not respond",
                                   r"no dey respond", r"\bfaint", r"collapsed", r"naghi eteta", r"naghi aza"],
    "severe_headache": [r"(severe|bad|serious|terrible|strong|very bad) headache",
                        r"head (dey |is )?(pain|ach|hurt)\w* (am |her )?(well well|too much|badly|seriously)",
                        r"headache (well well|too much|wey no gree)", r"isi na-?awa ya (nke ukwuu|ike|oke)"],
    "blurred_vision": [r"blur", r"(eye|eyes) (dey |are |is )?(dark|turn|blind)", r"can'?t see (well|clearly|properly)",
                       r"seeing (spots|stars|double|flashes)", r"anya (ya )?(na-?agba ya |na-?agba )?ochichiri"],
    "difficulty_breathing": [r"(difficult|difficulty|hard|can'?t|cannot|struggling to) (to )?breath",
                             r"short(ness)? of breath", r"breath dey (cut|hard)", r"ume na-?ekpo ya", r"naghi eku ume"],
    "severe_abdominal_pain": [r"(severe|serious|terrible|very bad|sharp) (belly|stomach|abdominal) pain",
                              r"belly (dey )?pain (am |her )?(well well|too much)", r"afo na-?awa ya (nke ukwuu|oke)"],
    "reduced_or_no_fetal_movement": [
        r"(baby|pikin|child|the baby|the pikin) (has |have )?(not|never|no longer|stopped|stop|no dey|don stop) (move|moving|moved|kick)",
        r"(no|reduced|less|decreased) (fetal |foetal |baby )?movement",
        r"(not|never|no longer) (felt|feel|feeling) (the )?(baby|pikin) (move|kick)",
        r"nwa (adighi|anaghi|naghi|o naghi) (emeghari|akpaghari|agaghari)"],
    "cord_or_limb_visible": [r"cord (is |dey )?(out|visible|coming|commot|showing)",
                             r"(hand|arm|leg|foot) (came|come|comes|dey commot|is coming|is) (out )?first"],
    "foul_or_green_fluid": [r"(smell|smells|smelling|stink|stinks)\w* (bad|foul|offensive)", r"\bfoul",
                            r"green (water|fluid|liquid)", r"(water|fluid) .{0,12}green"],
}
# Phrases that set a field to False (escalating the other way round).
RETAINED_PLACENTA = [r"placenta (has |have )?(never|not|no|still not|neva|never yet) (come|comot|commot|deliver|out)",
                     r"(placenta|afterbirth) (is )?(still )?(inside|stuck|retained)",
                     r"afterbirth (has |have )?(never|not|no) (come|comot|commot)"]

KNOWN_DRUG_WORDS = [
    "oxytocin", "pitocin", "syntocinon", "misoprostol", "cytotec", "magnesium", "mgso4", "ergometrine", "syntometrine",
    "carboprost", "tranexamic", "diazepam", "hydralazine", "nifedipine", "labetalol", "methyldopa", "aldomet",
    "ampicillin", "gentamicin", "metronidazole", "flagyl", "ceftriaxone", "amoxicillin", "augmentin", "paracetamol",
    "ibuprofen", "aspirin", "quinine", "artesunate", "coartem", "agbo", "herbal", "antibiotic", "antibiotics",
]
DOSE_PATTERN = re.compile(
    r"\b(\d+(\.\d+)?|half|one|two|three|four|five)\s*(-\s*\d+\s*)?"
    r"(mg|ml|g|iu|units?|mcg|micrograms?|µg|tablets?|tabs?|ampoules?|vials?|suppositor(y|ies)|drops?)\b",
    re.I,
)
NEGATION = re.compile(r"\b(no|not|without|never|denies|denied|nor|e no|she no|he no)\s+(\w+\s+){0,2}$")


def plain(text: str) -> str:
    """Lowercase and strip tone marks/diacritics (ọ -> o, ṅ -> n, ụ -> u)."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def screen_input(note: str) -> dict:
    if len(note) > config.MAX_NOTE_CHARS:
        return {"ok": False, "reason": f"Case note is longer than {config.MAX_NOTE_CHARS} characters."}
    lowered = note.lower()
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, lowered)
        if m:
            return {"ok": False, "reason": "Possible prompt-injection text detected; the note was not sent to the AI.",
                    "matched": m.group(0)}
    return {"ok": True}


def _find(pattern: str, text: str) -> str | None:
    for m in re.finditer(pattern, text):
        clause_start = max(text.rfind(c, 0, m.start()) for c in ".,;\n") + 1
        if not NEGATION.search(text[clause_start:m.start()]):
            return m.group(0)
    return None


def danger_word_scan(note: str) -> dict[str, tuple[bool, str]]:
    """field -> (value to force, words that triggered it). Negated mentions ("no bleeding") are skipped."""
    text = plain(note)
    hits: dict[str, tuple[bool, str]] = {}
    for field, pats in DANGER_WORDS.items():
        for pat in pats:
            words = _find(pat, text)
            if words:
                hits[field] = (True, words)
                break
    for pat in RETAINED_PLACENTA:
        m = re.search(pat, text)
        if m:
            hits["placenta_delivered"] = (False, m.group(0))
            break
    return hits


def _allowed_text(engine_result: dict) -> str:
    parts = []
    for t in engine_result.get("treatments", []):
        parts += [t.get("label", ""), t.get("dose", ""), t.get("how", ""), t.get("drug", "").replace("_", " ")]
    return plain(" ".join(parts))


def verify_messages(texts: dict[str, str], engine_result: dict) -> list[str]:
    """Return violations: medicines or doses in agent text that the protocol engine did not return."""
    allowed = _allowed_text(engine_result)
    problems = []
    for name, text in texts.items():
        low = plain(text or "")
        for drug in KNOWN_DRUG_WORDS:
            if re.search(rf"\b{drug}\b", low) and drug not in allowed:
                if drug in ("antibiotic", "antibiotics", "herbal", "agbo") and re.search(rf"(no|not|avoid|don'?t)\W+(\w+\W+){{0,3}}{drug}", low):
                    continue
                problems.append(f"{name}: mentions '{drug}', which the protocol engine did not return")
        for m in DOSE_PATTERN.finditer(low):
            phrase = re.sub(r"\s+", " ", m.group(0)).strip()
            number, unit_stem = re.escape(m.group(1).lower()), m.group(4).lower()[:2]
            if not re.search(rf"\b{number}\s*(-\s*\d+\s*)?\(?{unit_stem}", allowed):
                problems.append(f"{name}: dose '{phrase}' does not match the WHO protocol table")
    return problems

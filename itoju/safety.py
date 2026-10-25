"""Deterministic guardrails that run around the LLM agents.

1. screen_input      — blocks prompt-injection attempts and oversized notes before any model call.
2. danger_word_scan  — English/Pidgin keyword net for danger signs; anything it finds that the
                       intake agent missed is forced to True (it can only make care MORE urgent).
3. verify_messages   — every drug name and dose number in the agents' free text must come from
                       the iCCM engine's output; otherwise the text is replaced with a safe fallback.
"""
import re

from . import config

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (the |all |your )?(rules|instructions|protocol|guidelines)",
    r"(reveal|show|print|repeat) (me )?(your |the )?(system prompt|instructions|api key|prompt)",
    r"\byou are now\b|\bact as\b|\bpretend to be\b|\bdeveloper mode\b|\bjailbreak\b",
    r"\b(api[_ ]?key|portkey|openai_api_key)\b",
    r"new instructions?:|system:|<\s*/?\s*system\s*>",
]

DANGER_WORDS = {
    "convulsions": [r"convuls", r"seizure", r"\bfits?\b", r"\bjerk", r"dey shake", r"eyes? (roll|turn)"],
    "unable_to_drink_or_feed": [
        r"(can'?t|cannot|can not|unable to|not able to|no fit|won'?t|refus\w*( to)?) (drink|feed|eat|breast ?feed|suck)",
        r"not (drinking|feeding|breastfeeding|sucking)", r"no dey (drink|chop|suck)",
    ],
    "vomits_everything": [r"vomit\w* (everything|all|anything)", r"(throw|throws|throwing) (up )?everything",
                          r"everything (wey|that) (e|she|he) (chop|drink|eat)\w* (e|she|he)? ?(dey )?(vomit|throw)"],
    "unusually_sleepy_or_unconscious": [r"unconscious", r"won'?t wake", r"hard to wake", r"no dey wake",
                                        r"(very|unusually|too) (sleepy|drowsy)", r"\bfloppy\b", r"\blethargic\b",
                                        r"no (dey )?respond", r"not respond"],
    "blood_in_stool": [r"blood\w* (in|for|inside) (the |her |his )?(stool|poo|shit|toilet|purge)", r"bloody (stool|diarr)"],
    "swelling_both_feet": [r"(both|two) (feet|legs) (are |dey )?swell", r"swollen (feet|legs)", r"(feet|legs) (dey )?swell"],
    "chest_indrawing": [r"chest (in-?drawing|dey (pull|sink|go inside))", r"chest (pulls?|sinks?|goes) in",
                        r"lower chest (wall )?(pulls?|goes|sinks) in"],
}

KNOWN_DRUG_WORDS = [
    "amoxicillin", "artemether", "lumefantrine", "coartem", "artesunate", "ors", "zinc", "paracetamol",
    "ibuprofen", "aspirin", "cotrimoxazole", "septrin", "ciprofloxacin", "metronidazole", "flagyl",
    "chloroquine", "quinine", "ceftriaxone", "gentamicin", "amoxiclav", "augmentin", "ampicillin",
    "azithromycin", "antibiotic", "antimalarial", "syrup",
]
DOSE_PATTERN = re.compile(
    r"\b(\d+(\.\d+)?|half|one|two|three)\s*(-\s*\d+\s*)?(mg|ml|tablets?|tabs?|suppositor(y|ies)|cups?|teaspoons?|packets?)\b",
    re.I,
)


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


NEGATION = re.compile(r"\b(no|not|without|never|denies|denied|nor|e no|she no|he no)\s+(\w+\s+){0,2}$")


def danger_word_scan(note: str) -> dict[str, str]:
    """field -> the words in the note that triggered it. Negated mentions ("no chest indrawing") are skipped."""
    lowered = note.lower()
    hits = {}
    for field, pats in DANGER_WORDS.items():
        for pat in pats:
            for m in re.finditer(pat, lowered):
                clause_start = max(lowered.rfind(c, 0, m.start()) for c in ".,;\n") + 1
                if NEGATION.search(lowered[clause_start:m.start()]):
                    continue
                hits[field] = m.group(0)
                break
            if field in hits:
                break
    return hits


def _allowed_text(engine_result: dict) -> str:
    parts = []
    for t in engine_result.get("treatments", []):
        parts += [t.get("label", ""), t.get("dose", ""), t.get("how", ""), t.get("drug", "").replace("_", " ")]
    return " ".join(parts).lower()


def verify_messages(texts: dict[str, str], engine_result: dict) -> list[str]:
    """Return a list of violations: drugs or doses in agent text that the engine did not prescribe."""
    allowed = _allowed_text(engine_result)
    problems = []
    for name, text in texts.items():
        low = (text or "").lower()
        for drug in KNOWN_DRUG_WORDS:
            if re.search(rf"\b{drug}\b", low) and drug not in allowed:
                if drug in ("antibiotic", "antimalarial") and ("no " + drug in low or "not " in low):
                    continue
                problems.append(f"{name}: mentions '{drug}', which the iCCM engine did not prescribe")
        for m in DOSE_PATTERN.finditer(low):
            phrase = re.sub(r"\s+", " ", m.group(0)).strip()
            number, unit_stem = re.escape(m.group(1).lower()), m.group(4).lower()[:3]
            if not re.search(rf"\b{number}\s*(-\s*\d+\s*)?{unit_stem}", allowed):
                problems.append(f"{name}: dose '{phrase}' does not match the iCCM dosing table")
    return problems

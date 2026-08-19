"""Triage classification (Sprint 3).

Two layers, per the safety rules in CLAUDE.md:
1. GPT-4o returns structured JSON {triage, reason, reply} each turn.
2. A deterministic red-flag keyword net forces EMERGENCY before the LLM
   is even consulted — emergency detection must not depend on a model
   call succeeding or parsing cleanly.

Anything malformed or ambiguous escalates UP (CLINIC), never down.
"""

import json
import re
from dataclasses import dataclass
from enum import Enum


class TriageLevel(str, Enum):
    PENDING = "PENDING"  # still asking questions
    SELF_CARE = "SELF_CARE"
    CLINIC = "CLINIC"
    EMERGENCY = "EMERGENCY"


@dataclass
class TriageResult:
    level: TriageLevel
    reason: str
    reply: str
    language: str = ""  # LLM-reported user language, "" when absent


# Conservative safety net across the five supported languages.
# Substring match on lowercased text; the LLM remains the primary
# classifier — this only ever escalates, never de-escalates.
RED_FLAGS = (
    # English
    "convuls", "seizure", "unconscious", "not breathing", "can't breathe",
    "cannot breathe", "chest pain", "severe bleeding", "bleeding a lot",
    "snake bite", "snakebite", "snake bit", "snake", "poison",
    # Stroke (English)
    "face droop", "face drooping", "face sagging", "drooping face",
    "slurred speech", "slurring", "can't talk", "cannot talk", "can't speak",
    "cannot speak", "one side weak", "side of the body weak", "arm weak",
    "arm weakness", "sudden weakness", "sudden numbness", "weak on one side",
    # Anaphylaxis / severe allergy (English)
    "face swelling", "lips swelling", "lip swelling", "tongue swelling",
    "throat swelling", "throat closing", "can't swallow", "cannot swallow",
    "difficulty swallowing", "severe allergic reaction", "swelling after",
    # Newborn / infant danger signs (English)
    "not feeding", "refusing to feed", "won't feed", "won't cry",
    "too weak to cry", "very weak baby", "jaundice", "yellow eyes",
    "yellow skin", "cord bleeding", "umbilical bleeding", "lethargic",
    "won't wake to feed", "not waking to feed", "will not breastfeed",
    "won't breastfeed", "not breastfeed", "refusing to breastfeed",
    # Head injury / repeated vomiting (English)
    "hit head", "head injury", "vomiting repeatedly", "keeps vomiting",
    "vomiting a lot", "repeated vomiting", "vomiting nonstop",
    "throwing up a lot",
    # Stroke: movement/speech failure (English)
    "can't move", "cannot move", "can't move arm", "cannot move arm",
    "can't move leg", "cannot move leg", "can't move one side",
    "cannot move one side", "speech not clear", "speech unclear",
    "not speaking clearly", "speech is not clear",
    # Poisoning / chemical ingestion (English + Pidgin)
    "swallowed poison", "drank poison", "swallow kerosene", "swallow fuel",
    "swallow chemical", "swallow bleach", "swallow detergent",
    "drink kerosene", "drink fuel", "drink bleach", "drink chemical",
    "swallow medicine bottle", "swallow medicine", "swallow pill",
    "swallow tablet", "drink medicine", "drink detergent",
    # Pidgin
    "no dey breathe", "no fit breathe", "dey shake body", "shake body",
    "no dey wake", "no fit wake", "don faint",
    "face dey drop", "face dey sag", "one side dey weak", "no fit talk",
    "mouth dey turn", "dey drag one leg", "face dey swell", "lip dey swell",
    "throat dey close", "no dey suck", "no dey feed", "pikin dey yellow",
    "yellow pikin", "pikin too weak", "no dey cry", "blood dey comot",
    "blood just dey comot", "blood dey come", "dey vomit plenty",
    "dey vomit nonstop", "vomit plenty",
    # Hausa
    "farfadiya", "ba ya numfashi", "ciwon kirji", "zub da jini", "suma",
    "fuska ta karkata", "rauni a gefe", "ba ya iya magana", "fuska ta kumbura",
    "makogwaro ya kumbura", "bai sha nono ba", "ba ya shan nono",
    "ba ya sha nono", "ya yi shiru", "zafi sosai", "maciji",
    "amai ba tsayawa", "jiki ya yi rawaya",
    # Yoruba
    "gìrì", " giri", "daku", "dákú", "ko le mi", "kò mí", "eje pupo",
    "irora aya",
    "oju ro", "apa kan ko lagbara", "ko le soro", "oju wu", "eti wu",
    "ofun wu", "ko mu omi", "ko jeun", "ejo bu", "ejò", "ara po",
    # Yoruba: infant feeding refusal + bleeding (incl. pregnancy)
    "ko lati mu omu", "ko mu omu", "ko gba omu", "ko mu oyan",
    "eje n jade", "eje n jade lara", "eje n bo", "eje n san",
    # Igbo
    "ọgbọ", "ogbo aki", "adaa mba", "naghị eku ume", "naghi eku ume",
    "ekuchaghị ume", "enweghị ike iku ume", "ọbara na-agba", "obara na-agba",
    "mgbu obi", "ọ nwụọla", "adịghị eteta", "adighi eteta",
    "ihu na-adagbu", "aka nwa adịghị ike", "enweghị ike ikwu okwu",
    "ihu na-aza aza", "akpịrị na-afụ", "anaghị enye nwa ara",
    "ahụ na-acha odo odo", "nwa adịghị ike", "agwọ",
    # Igbo: newborn feeding refusal + very hot
    "ọ naghị aṅụ ara", "naghị aṅụ ara", "adịghị aṅụ ara",
    "anaghị aṅụ ara", "ọ na-ekpo ọkụ nke ukwuu", "na-ekpo ọkụ nke ukwuu",
    "ọbara na-apụ", "obara na-apụ",
)

# Hausa/Yoruba/Igbo texts are draft translations — have native speakers
# verify them before deployment/evaluation (note for SUS participants).
EMERGENCY_OVERRIDE_REPLIES = {
    "english": (
        "🚨 EMERGENCY — GO NOW\n\n"
        "What you described could be a serious emergency. Do not wait, do "
        "not keep typing — take the person to the NEAREST hospital or "
        "health centre right now, or get someone to take you."
    ),
    "pidgin": (
        "🚨 EMERGENCY — GO NOW / WAKA GO HOSPITAL SHARP SHARP\n\n"
        "Wetin you describe fit be serious emergency. No wait, no type again — "
        "carry the person go the NEAREST hospital or health centre right now, "
        "or find help make dem carry una go."
    ),
    "hausa": (
        "🚨 GAGGAWA — JE ASIBITI YANZU\n\n"
        "Abin da ka bayyana na iya zama babbar gaggawa. Kada ka jira — "
        "kai mutumin ASIBITI ko cibiyar lafiya mafi kusa YANZU, ko nemi "
        "wanda zai kai ku."
    ),
    "yoruba": (
        "🚨 PAJAWIRI — LO SI ILE-IWOSAN BAYII\n\n"
        "Ohun ti o so le je pajawiri nla. Ma duro — gbe eniyan naa lo si "
        "ILE-IWOSAN tabi ile-ise ilera to sunmo julo BAYII, tabi wa eni "
        "ti yoo gbe yin lo."
    ),
    "igbo": (
        "🚨 IHE MBERE — GAA ỤLỌ ỌGWỤ UGBU A\n\n"
        "Ihe ị kwuru nwere ike ịbụ ihe mberede dị njọ. Echela — buru onye "
        "ahụ gaa ỤLỌ ỌGWỤ ma ọ bụ ebe ahụike kacha nso UGBU A, ma ọ bụ "
        "chọta onye ga-eburu unu gaa."
    ),
}

CLINIC_FALLBACK_REPLY = (
    "I no too sure how serious this one be, so make we play am safe: "
    "abeg go see a health worker for the nearest clinic today."
)

BANNERS = {
    TriageLevel.SELF_CARE: "✅ SELF-CARE",
    TriageLevel.CLINIC: "⚠️ VISIT CLINIC TODAY",
    TriageLevel.EMERGENCY: "🚨 EMERGENCY — GO NOW",
}


def matched_red_flag(text: str) -> str | None:
    """The red-flag term present in the message, if any."""
    lowered = text.lower()
    return next((flag for flag in RED_FLAGS if flag in lowered), None)


def contains_red_flag(text: str) -> bool:
    return matched_red_flag(text) is not None


# Red-flag terms mapped to the clinical sign they indicate, so
# surveillance records carry a meaningful symptom rather than a generic
# "keyword detected". Keyed by substring of the matched flag.
RED_FLAG_SIGNS = (
    (("convuls", "seizure", "farfadiya", "gìrì", " giri", "shake body", "dey shake"), "convulsion"),
    (("unconscious", "no dey wake", "no fit wake", "faint", "suma", "daku", "dákú"), "unconscious / not waking"),
    (("breathe", "breathing", "numfashi", "ko le mi", "kò mí"), "difficulty breathing"),
    (("chest pain", "ciwon kirji", "irora aya"), "chest pain"),
    (("bleeding", "zub da jini", "eje pupo", "blood"), "severe bleeding"),
    (("snake", "poison", "ejo bu", "ejò", "maciji", "agwọ"), "snake bite / poisoning"),
    (("face droop", "face drooping", "face sagging", "drooping face", "face dey drop", "face dey sag",
      "oju ro", "fuska ta karkata", "ihu na-adagbu"), "stroke signs (face)"),
    (("slurred", "slurring", "no fit talk", "can't talk", "cannot talk", "mouth dey turn",
      "ba ya iya magana", "ko le soro", "enweghị ike ikwu okwu"), "stroke signs (speech)"),
    (("one side weak", "arm weak", "arm weakness", "side of the body weak", "sudden weakness",
      "sudden numbness", "weak on one side", "rauni a gefe", "apa kan ko lagbara",
      "aka nwa adịghị ike"), "stroke signs (weakness)"),
    (("face swelling", "lips swelling", "lip swelling", "tongue swelling", "throat swelling",
      "throat closing", "face dey swell", "lip dey swell", "throat dey close",
      "fuska ta kumbura", "makogwaro ya kumbura", "oju wu", "eti wu", "ofun wu",
      "ihu na-aza aza", "akpịrị na-afụ"), "severe allergic reaction (swelling)"),
    (("can't swallow", "cannot swallow", "difficulty swallowing"), "severe allergic reaction (swallowing)"),
    (("not feeding", "refusing to feed", "won't feed", "no dey suck", "no dey feed",
      "bai sha nono ba", "ba ya shan nono", "ba ya sha nono", "ko mu omi",
      "ko jeun", "anaghị enye nwa ara", "will not breastfeed",
      "won't breastfeed", "not breastfeed", "refusing to breastfeed"),
     "newborn not feeding"),
    (("jaundice", "yellow eyes", "yellow skin", "pikin dey yellow", "yellow pikin",
      "jiki ya yi rawaya", "ara po", "ahụ na-acha odo odo"), "newborn jaundice"),
    (("cord bleeding", "umbilical bleeding"), "newborn cord bleeding"),
    (("lethargic", "too weak to cry", "very weak baby", "won't wake to feed",
      "not waking to feed", "pikin too weak", "nwa adịghị ike",
      "ya yi shiru"), "newborn lethargy / weakness"),
    (("hit head", "head injury", "vomiting repeatedly", "keeps vomiting",
      "vomiting a lot", "repeated vomiting", "vomiting nonstop",
      "throwing up a lot", "dey vomit plenty", "dey vomit nonstop",
      "vomit plenty", "amai ba tsayawa"),
     "repeated vomiting / possible head injury"),
    (("zafi sosai",), "high fever (very hot)"),
    (("ọgbọ", "adaa mba", "ogbo aki"), "convulsion"),
    (("eku ume", "iku ume", "ekuchaghị ume"), "difficulty breathing"),
    (("mgbu obi",), "chest pain"),
    (("ọbara na-agba", "obara na-agba"), "severe bleeding"),
    (("adịghị eteta", "adighi eteta", "nwụọla"), "unconscious / not waking"),
)

# Deterministic downgrade guard: presentations where the model must NEVER
# return SELF_CARE. These are deliberately broad; the guard can only
# escalate, never de-escalate.
HIGH_RISK_SIGNALS = (
    "chest",          # chest pain / tightness
    "pregnan",        # pregnancy (any bleeding/labour concern escalates)
    "labour",         # labour
    "newborn", "infant", "weeks old", "days old",  # young infant age band
    "breath",         # breathing difficulty / fast breathing
)


def guard_verdict(level: TriageLevel, history_text: str, user_turns: int):
    """Deterministic post-parse guard.

    Returns (guarded_level, reason, changed). The LLM can under-triage
    (demonstrated on the full vignette corpus), so this rule layer:
      1. re-scans the whole conversation for any danger-sign keyword and
         forces EMERGENCY;
      2. refuses SELF_CARE when the conversation contains a high-risk
         presentation (escalates to CLINIC);
      3. refuses SELF_CARE before at least two user turns, so a case is
         never reassured after a single message.
    The guard only escalates — never downgrades.
    """
    lowered = (history_text or "").lower()
    matched = matched_red_flag(lowered)
    if matched:
        return (
            TriageLevel.EMERGENCY,
            f"Red-flag danger sign in conversation history: {_sign_for(matched)} (deterministic safety net)",
            True,
        )
    if level == TriageLevel.SELF_CARE:
        if any(signal in lowered for signal in HIGH_RISK_SIGNALS):
            return (
                TriageLevel.CLINIC,
                "High-risk presentation in conversation — escalated up by default",
                True,
            )
        if user_turns < 2:
            return (
                TriageLevel.CLINIC,
                "Insufficient information — escalated up by default",
                True,
            )
    return level, "", False


# Localised safe replies used when the guard overrides a SELF_CARE
# verdict. Draft translations — verify with native speakers.
CLINIC_FALLBACKS = {
    "english": (
        "I am not sure how serious this is, so let us play it safe: "
        "please see a health worker at the nearest clinic today."
    ),
    "pidgin": CLINIC_FALLBACK_REPLY,
    "hausa": (
        "Ban tabbatar da yadda lamarin yake ba, don haka mu yi taka tsantsan: "
        "don Allah je wurin ma'aikacin lafiya a asibiti mafi kusa a yau."
    ),
    "yoruba": (
        "Mi o da idanmo pe bi oro naa se ri, nitori naa je ki a so a mura: "
        "jowo lo si osise ilera ni ile-iwosan to sunmo loni."
    ),
    "igbo": (
        "Amaghị m otú ihe a siri dị, yabụ ka anyị kpachara anya: "
        "biko gaa hụ onye ọrụ ahụike n'ụlọ ọgwụ kacha nso taa."
    ),
}


def _sign_for(flag: str) -> str:
    for needles, sign in RED_FLAG_SIGNS:
        if any(n in flag for n in needles):
            return sign
    return flag


def emergency_override(language: str = "pidgin", matched: str | None = None) -> TriageResult:
    reply = EMERGENCY_OVERRIDE_REPLIES.get(language, EMERGENCY_OVERRIDE_REPLIES["pidgin"])
    sign = _sign_for(matched) if matched else "danger sign"
    return TriageResult(
        level=TriageLevel.EMERGENCY,
        reason=f"Red-flag danger sign detected: {sign} (deterministic safety net)",
        reply=reply,
        language=language,
    )


def parse_triage_response(raw: str) -> TriageResult:
    """Parse the model's JSON. Anything broken escalates to CLINIC."""
    try:
        payload = json.loads(_extract_json(raw))
        level = TriageLevel(str(payload["triage"]).strip().upper())
        reply = str(payload["reply"]).strip()
        if not reply:
            raise ValueError("empty reply")
        language = str(payload.get("language", "")).strip().lower()
        if language not in EMERGENCY_OVERRIDE_REPLIES:
            language = ""
        return TriageResult(
            level=level,
            reason=str(payload.get("reason", "")).strip(),
            reply=reply,
            language=language,
        )
    except (ValueError, KeyError, TypeError):
        return TriageResult(
            level=TriageLevel.CLINIC,
            reason="Unparseable model output — escalated up by default",
            reply=CLINIC_FALLBACK_REPLY,
        )


def format_reply(result: TriageResult) -> str:
    """Final triage decisions get their banner; questions go out bare."""
    if result.level == TriageLevel.PENDING:
        return result.reply
    if result.reply.startswith(("🚨", "⚠️", "✅")):
        return result.reply
    return f"{BANNERS[result.level]}\n\n{result.reply}"


def _extract_json(raw: str) -> str:
    """Tolerate markdown code fences and prose around the JSON object."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text

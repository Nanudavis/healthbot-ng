"""USSD flow (Sprint 7) — Africa's Talking gateway.

Protocol: AT POSTs form fields {sessionId, phoneNumber, text} where
text is the full "*"-joined history of the user's inputs. Replies are
plain text: "CON <menu>" to continue the session, "END <verdict>" to
finish it.

Provider context: this is developed and tested against Africa's Talking's
simulator because a live Nigerian shortcode requires NCC/telecom
approval. The simulator implements the same CON/END protocol and the
same "*"-joined text history, so the flow logic below is identical in
production; only the billing changes (live USSD charges ~₦6.98 per
120-second session, so screens are counted as the budget proxy).

The flow is fully deterministic — numbered menus only, no free text,
no model call — so it works on a ₦0-data phone, inside gateway
timeouts, and with every upstream API down. It shares the triage
semantics of app.triage: any danger sign (or "not sure") means
EMERGENCY, and ambiguity always escalates up, never down.

Invalid inputs are simply not consumed, so the same menu is shown
again on the next screen.
"""

from app import records, safety_net
from app.sessions import SessionStore
from app.triage import TriageLevel

LANGS = {"1": "english", "2": "pidgin", "3": "hausa", "4": "yoruba", "5": "igbo"}

WHO_ME, WHO_CHILD, WHO_OTHER = "1", "2", "3"
SYM_FEVER, SYM_COUGH, SYM_DIARRHOEA, SYM_PAIN, SYM_INJURY, SYM_OTHER = (
    "1", "2", "3", "4", "5", "6"
)
DANGER_YES, DANGER_NO, DANGER_UNSURE = "1", "2", "3"
DUR_TODAY, DUR_FEW_DAYS, DUR_LONG = "1", "2", "3"

MAX_SCREEN_CHARS = 182  # Africa's Talking per-screen limit

# "0" backs out of the last choice — one wrong tap should not force a
# full restart. At the language screen it simply returns there.
BACK = "0"
# A wrong tap costs a screen, and live USSD charges per session, so
# repeated invalid replies end the session instead of looping forever.
MAX_CONSECUTIVE_INVALID = 3
TOO_MANY_INVALID = "Too many invalid replies. Please dial again to restart."

# Every verdict ends with this (a non-negotiable safety rule), so it
# lives here rather than being repeated inside each screen where one
# could quietly go missing.
DISCLAIMERS = {
    "english": "Guidance only - not a doctor.",
    "pidgin": "Na guidance only - no be doctor.",
    "hausa": "Shawara ce kawai - ba likita ba.",
    "yoruba": "Imoran nikan - kii se dokita.",
    "igbo": "Ndụmọdụ nanị - ọ bụghị dọkịta.",
}

LANGUAGE_MENU = (
    "Welcome to HealthBot NG\n"
    "Choose your language:\n"
    "1. English\n2. Pidgin\n3. Hausa\n4. Yoruba\n5. Igbo"
)

# Hausa/Yoruba/Igbo are draft translations — verify with native speakers
# before deployment/evaluation.
SCREENS = {
    "english": {
        "who": "Who is sick?\n1. Me\n2. My child (under 5)\n3. Someone else",
        "symptom": (
            "Main problem?\n1. Fever\n2. Cough/breathing\n3. Diarrhoea/vomiting\n"
            "4. Body pain\n5. Injury/bleeding\n6. Other"
        ),
        "danger": (
            "Any of these: convulsion, can't wake, can't drink, hard "
            "breathing, much blood?\n1. Yes\n2. No\n3. Not sure"
        ),
        "duration": "How long?\n1. Started today\n2. 1-3 days\n3. Over 3 days",
        "emergency": (
            "EMERGENCY: Go to the NEAREST hospital NOW. Do not wait."
        ),
        "clinic": (
            "VISIT CLINIC: See a health worker TODAY."
        ),
        "self": (
            "SELF-CARE: Rest and drink fluids."
        ),
    },
    "pidgin": {
        "who": "Who dey sick?\n1. Na me\n2. My pikin (under 5)\n3. Another person",
        "symptom": (
            "Wetin dey worry?\n1. Body hot (fever)\n2. Cough/breathing\n"
            "3. Purging/vomit\n4. Body pain\n5. Wound/blood\n6. Another thing"
        ),
        "danger": (
            "Any of these dey? Shake body, no fit wake, no fit drink, hard "
            "breathing, plenty blood\n1. Yes\n2. No\n3. I no sure"
        ),
        "duration": "How long e don dey?\n1. E start today\n2. 1-3 days\n3. Pass 3 days",
        "emergency": (
            "EMERGENCY: Waka go the NEAREST hospital NOW. No wait."
        ),
        "clinic": (
            "GO CLINIC: See health worker TODAY."
        ),
        "self": (
            "SELF-CARE: Rest, drink water well well."
        ),
    },
    "hausa": {
        "who": "Wanene ba shi da lafiya?\n1. Ni ne\n2. Yarona (kasa da 5)\n3. Wani mutum",
        "symptom": (
            "Menene matsalar?\n1. Zazzabi\n2. Tari/numfashi\n3. Gudawa/amai\n"
            "4. Ciwon jiki\n5. Rauni/jini\n6. Wani abu"
        ),
        "danger": (
            "Akwai daya daga cikin wadannan? Farfadiya, baya farkawa, baya "
            "sha, wahalar numfashi, jini mai yawa\n1. Eh\n2. A'a\n3. Ban sani ba"
        ),
        "duration": "Tun yaushe?\n1. Yau ya fara\n2. Kwana 1-3\n3. Fiye da kwana 3",
        "emergency": (
            "GAGGAWA: Je ASIBITI mafi kusa YANZU. Kada ka jira."
        ),
        "clinic": (
            "JE ASIBITI: Ga ma'aikacin lafiya YAU."
        ),
        "self": (
            "KULA DA KAI: Huta, sha ruwa."
        ),
    },
    "yoruba": {
        "who": "Tani ko da ara?\n1. Emi ni\n2. Omo mi (labe odun 5)\n3. Elomiran",
        "symptom": (
            "Kini isoro naa?\n1. Iba\n2. Iko/emi\n3. Igbe gbuuru/eebi\n"
            "4. Irora ara\n5. Ogbe/eje\n6. Nkan miran"
        ),
        "danger": (
            "Nkan wonyi nko? Giri, ko le ji, ko le mu omi, emi lile, eje "
            "pupo\n1. Beeni\n2. Rara\n3. Mi o mo"
        ),
        "duration": "Lati igba wo?\n1. O bere loni\n2. Ojo 1-3\n3. Ju ojo 3 lo",
        "emergency": (
            "PAJAWIRI: Lo si ILE-IWOSAN to sunmo BAYII. Ma duro."
        ),
        "clinic": (
            "LO SI ILE-IWOSAN: Ri osise ilera LONI."
        ),
        "self": (
            "ITOJU ARA ENI: Sinmi, mu omi."
        ),
    },
"igbo": {
        "who": "Onye na-arịa?\n1. Ọ bụ m\n2. Nwa m (n'okpuru 5)\n3. Onye ọzọ",
        "symptom": (
            "Gịnị bụ nsogbu?\n1. Ahụ ọkụ\n2. Ụkwara/iku ume\n3. Afọ ọsịsa/agbọ\n"
            "4. Mgbu ahụ\n5. Mmerụ ahụ/ọbara\n6. Ihe ọzọ"
        ),
        "danger": (
            "Nke a ọ dị? Ọgbọ, adịghị eteta, adịghị aṅụ, iku ume ike, "
            "ọbara buru ibu\n1. Ee\n2. Mba\n3. Amaghị m"
        ),
        "duration": "Kemgbe ole? \n1. Malitere taa\n2. Ụbọchị 1-3\n3. Karịa ụbọchị 3",
        "emergency": (
            "IHE MBERE: Gaa ỤLỌ ỌGWỤ kacha nso UGBU A. Echela."
        ),
        "clinic": (
            "GAA ỤLỌ ỌGWỤ: Hụ onye ahụike TAA."
        ),
        "self": (
            "LEKỌTA ONWE GỊ: Zuru ike, ṅụọ mmiri."
        ),
    },
}


def decide(who: str, symptom: str, danger: str, duration: str) -> TriageLevel:
    """Deterministic IMCI-style rule table. Conservative by design."""
    if danger in (DANGER_YES, DANGER_UNSURE):
        return TriageLevel.EMERGENCY
    if symptom == SYM_INJURY:
        return TriageLevel.CLINIC
    if symptom == SYM_FEVER:
        # Malaria-endemic setting: every fever needs a test.
        return TriageLevel.CLINIC
    if who == WHO_CHILD and symptom in (SYM_COUGH, SYM_DIARRHOEA):
        return TriageLevel.CLINIC
    if duration == DUR_LONG:
        return TriageLevel.CLINIC
    return TriageLevel.SELF_CARE


def handle_ussd(session_id: str, phone_number: str, text: str) -> str:
    steps = [s for s in text.split("*") if s] if text else []

    lang = who = symptom = danger = duration = None
    invalid_streak = 0
    phone_hash = SessionStore.anonymise(phone_number)

    def valid_for(step: str, field: str) -> bool:
        checks = {
            "lang": lambda s: s in LANGS,
            "who": lambda s: s in (WHO_ME, WHO_CHILD, WHO_OTHER),
            "symptom": lambda s: len(s) == 1 and s in "123456",
            "danger": lambda s: s in (DANGER_YES, DANGER_NO, DANGER_UNSURE),
            "duration": lambda s: s in (DUR_TODAY, DUR_FEW_DAYS, DUR_LONG),
        }
        return checks[field](step)

    for step in steps:
        if step == BACK:
            # Pop the most recent assignment; nothing else changes.
            if duration is not None:
                duration = None
            elif danger is not None:
                danger = None
            elif symptom is not None:
                symptom = None
            elif who is not None:
                who = None
            else:
                lang = None
            invalid_streak = 0
            continue
        if lang is None:
            field = "lang"
        elif who is None:
            field = "who"
        elif symptom is None:
            field = "symptom"
        elif danger is None:
            field = "danger"
        else:
            field = "duration"
        if valid_for(step, field):
            if field == "lang":
                lang = LANGS[step]
            elif field == "who":
                who = step
            elif field == "symptom":
                symptom = step
            elif field == "danger":
                danger = step
            else:
                duration = step
            invalid_streak = 0
        else:
            invalid_streak += 1

    # A returning user skips the language screen; "0" there returns to it.
    saved_lang = records.channel_preference(phone_hash, "ussd")
    if lang is None and not text and saved_lang:
        lang = saved_lang
    if lang and lang != (saved_lang or ""):
        records.save_channel_preference(phone_hash, "ussd", lang)

    def menu_or_give_up(screen: str) -> str:
        if invalid_streak >= MAX_CONSECUTIVE_INVALID:
            return f"END {TOO_MANY_INVALID}"
        return f"CON {screen}"

    if lang is None:
        return menu_or_give_up(LANGUAGE_MENU)
    screens = SCREENS[lang]
    if who is None:
        return menu_or_give_up(screens["who"])
    if symptom is None:
        return menu_or_give_up(screens["symptom"])
    if danger is None:
        return menu_or_give_up(screens["danger"])
    if danger in (DANGER_YES, DANGER_UNSURE):
        _log(phone_number, lang, TriageLevel.EMERGENCY, who, symptom, danger, None)
        return f"END {_with_return_signs(screens['emergency'], lang, TriageLevel.EMERGENCY, who)}"
    if duration is None:
        return menu_or_give_up(screens["duration"])

    level = decide(who, symptom, danger, duration)
    _log(phone_number, lang, level, who, symptom, danger, duration)
    key = {
        TriageLevel.EMERGENCY: "emergency",
        TriageLevel.CLINIC: "clinic",
        TriageLevel.SELF_CARE: "self",
    }[level]
    return f"END {_with_return_signs(screens[key], lang, level, who)}"


def _with_return_signs(verdict: str, lang: str, level: TriageLevel, who: str) -> str:
    """Attach the signs that mean come back immediately.

    Emergencies are already being sent now, so return advice would only
    dilute the instruction. Everyone else is going home with a plan and
    needs to know what would change it.
    """
    disclaimer = DISCLAIMERS.get(lang, DISCLAIMERS["english"])
    if level == TriageLevel.EMERGENCY:
        return f"{verdict}\n{disclaimer}"
    advice = safety_net.advice_line(lang, is_child=(who == WHO_CHILD))
    return f"{verdict}\n{advice}\n{disclaimer}"


# English labels for the anonymised record's reason field — built from
# menu codes only, never from anything the user typed.
WHO_LABELS = {WHO_ME: "adult (self)", WHO_CHILD: "child under 5", WHO_OTHER: "another person"}
SYMPTOM_LABELS = {
    SYM_FEVER: "fever",
    SYM_COUGH: "cough/breathing",
    SYM_DIARRHOEA: "diarrhoea/vomiting",
    SYM_PAIN: "body pain",
    SYM_INJURY: "injury/bleeding",
    SYM_OTHER: "other",
}
DANGER_LABELS = {
    DANGER_YES: "danger sign present",
    DANGER_NO: "no danger sign",
    DANGER_UNSURE: "danger sign uncertain",
}
DURATION_LABELS = {DUR_TODAY: "started today", DUR_FEW_DAYS: "1-3 days", DUR_LONG: "over 3 days"}


def _log(phone_number, lang, level, who, symptom, danger, duration) -> None:
    parts = [WHO_LABELS.get(who, ""), SYMPTOM_LABELS.get(symptom, ""), DANGER_LABELS.get(danger, "")]
    if duration:
        parts.append(DURATION_LABELS.get(duration, ""))
    records.log_triage(
        session_id=SessionStore.anonymise(phone_number),
        channel="ussd",
        language=lang,
        level=level.value,
        reason=", ".join(p for p in parts if p),
    )

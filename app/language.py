"""Lightweight language detection (Sprint 5).

GPT-4o natively detects and follows the user's language — there is no
translation layer. This heuristic covers what the LLM can't:
- localising the deterministic safety messages (red-flag emergency
  override, API-down fallback), which must work with no model call;
- tagging sessions by language for the research analytics dashboard and the
  per-language accuracy comparison in Chapter 5.

The LLM also reports the language in its JSON each turn; that report
overrides this heuristic when present.
"""

import re

ENGLISH = "english"
PIDGIN = "pidgin"
HAUSA = "hausa"
YORUBA = "yoruba"
IGBO = "igbo"
SUPPORTED = {ENGLISH, PIDGIN, HAUSA, YORUBA, IGBO}

# Distinctive single words are matched as whole tokens; phrases as
# substrings. Deliberately conservative — ambiguous words are omitted.
WORD_MARKERS = {
    PIDGIN: {
        "abeg", "wetin", "dey", "pikin", "una", "wahala", "sabi",
        "chop", "waka", "dem", "oya", "wey",
    },
    HAUSA: {
        "yana", "tana", "ina", "ciwo", "zazzabi", "jiki", "yaro",
        "yarona", "yarinya", "asibiti", "lafiya", "hakuri", "numfashi",
        "gida", "magani",
    },
    YORUBA: {
        "omo", "iba", "oogun", "aisan", "pupo", "gbona", "dokita",
        "owo", "inu", "ile-iwosan",
    },
    IGBO: {
        "nwa", "nwam", "ahu", "ahụ", "ọrịa", "oria", "ogwu", "ọgwụ",
        "isi", "afo", "afọ", "ekwughi", "ọkụ", "oku", "ụlọ", "ulo",
        "dọkịta", "dokita", "obi", "mmiri", "biko",
    },
}
PHRASE_MARKERS = {
    PIDGIN: ("no fit", "e don", "e no", "sharp sharp", "small small", "how far"),
    HAUSA: ("ba ya", "ba ta", "ba zan", "yi hakuri"),
    YORUBA: ("mo ni", "o ti", "ko le", "ni ile"),
    IGBO: ("nwa m", "ahụ m", "ahu m", "ọ na-", "o na-", "adịghị", "adighi", "enwe ike"),
}
# Orthography: Hausa hooked letters; Yoruba dotted/tonal vowels.
# Igbo deliberately has none — it shares ọ/ụ with Yoruba, so its dotted
# vowels would misattribute Yoruba text. Word and phrase markers carry it.
CHAR_MARKERS = {
    HAUSA: "ɓɗƙƴ",
    YORUBA: "ẹọṣàáèéìíòóùụ́́̀",
}


def detect_language(text: str) -> str | None:
    """Best-guess language, or None when there is no clear marker
    (callers then fall back to the session's last known language)."""
    lowered = text.lower()
    tokens = set(re.findall(r"[\w'-]+", lowered, re.UNICODE))

    scores = {}
    for lang in (PIDGIN, HAUSA, YORUBA, IGBO):
        score = len(WORD_MARKERS[lang] & tokens)
        score += sum(1 for phrase in PHRASE_MARKERS[lang] if phrase in lowered)
        score += sum(1 for ch in CHAR_MARKERS.get(lang, "") if ch in lowered)
        scores[lang] = score

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None

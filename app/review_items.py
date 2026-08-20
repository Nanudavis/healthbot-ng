"""Native-speaker review content: the exact strings each reviewer must see.

Single source of truth for the review package (docx/Google Docs/web form) so
the three artefacts can never drift apart.
"""
import csv
import re

from app import language, safety_net, triage

GLOSS = {
    "hausa": {
        "farfadiya": "fits / convulsion", "ba ya numfashi": "not breathing",
        "ciwon kirji": "chest pain", "zub da jini": "bleeding",
        "suma": "fainted / unconscious", "fuska ta karkata": "face twisted (stroke sign)",
        "rauni a gefe": "weakness on one side", "ba ya iya magana": "cannot speak",
        "fuska ta kumbura": "face swollen", "makogwaro ya kumbura": "throat swollen",
        "bai sha nono ba": "not taking breast (infant)", "ba ya shan nono": "not taking breast (infant)",
        "ba ya sha nono": "not taking breast (infant)", "ya yi shiru": "unusually quiet (infant)",
        "zafi sosai": "very hot (fever)", "maciji": "snake",
        "amai ba tsayawa": "vomiting that will not stop", "jiki ya yi rawaya": "body turned yellow",
    },
    "yoruba": {
        "gìrì": "fits / convulsion", "giri": "fits / convulsion",
        "daku": "fainted", "dákú": "fainted",
        "ko le mi": "cannot breathe", "kò mí": "cannot breathe", "eje pupo": "heavy bleeding",
        "irora aya": "chest pain", "oju ro": "face drooping (stroke sign)",
        "apa kan ko lagbara": "one side weak", "ko le soro": "cannot speak",
        "oju wu": "face swollen", "eti wu": "ear swollen", "ofun wu": "throat swollen",
        "ko mu omi": "not drinking water", "ko jeun": "not eating", "ejo bu": "snake bite",
        "ejò": "snake", "ara po": "body swollen", "ko lati mu omu": "refuses to breastfeed",
        "ko mu omu": "does not breastfeed", "ko gba omu": "not taking the breast",
        "ko mu oyan": "not sucking", "eje n jade": "blood coming out",
        "eje n jade lara": "blood coming out of her body", "eje n bo": "bleeding",
        "eje n san": "bleeding",
    },
    "igbo": {
        "ọgbọ": "fits / convulsion", "ogbo aki": "fits / convulsion", "adaa mba": "fainted / collapsed",
        "naghị eku ume": "not breathing", "naghi eku ume": "not breathing",
        "ekuchaghị ume": "not breathing well", "enweghị ike iku ume": "cannot breathe",
        "ọbara na-agba": "bleeding", "obara na-agba": "bleeding", "mgbu obi": "chest pain",
        "ọ nwụọla": "he has died / unconscious", "adịghị eteta": "not waking up",
        "adighi eteta": "not waking up", "ihu na-adagbu": "face drooping (stroke sign)",
        "aka nwa adịghị ike": "child's arm weak", "enweghị ike ikwu okwu": "cannot speak",
        "ihu na-aza aza": "face swollen", "akpịrị na-afụ": "throat swollen",
        "anaghị enye nwa ara": "not giving baby breast (mother)", "ahụ na-acha odo odo": "body turned yellow",
        "nwa adịghị ike": "child weak", "agwọ": "snake",
        "ọ naghị aṅụ ara": "baby not drinking breast", "naghị aṅụ ara": "not drinking breast",
        "adịghị aṅụ ara": "not drinking breast", "anaghị aṅụ ara": "not drinking breast",
        "ọ na-ekpo ọkụ nke ukwuu": "very hot", "na-ekpo ọkụ nke ukwuu": "very hot",
        "ọbara na-apụ": "blood coming out", "obara na-apụ": "blood coming out",
    },
}

_RED_FLAG_SEGMENTS = None


def _red_flag_segments() -> dict[str, list[str]]:
    global _RED_FLAG_SEGMENTS
    if _RED_FLAG_SEGMENTS is not None:
        return _RED_FLAG_SEGMENTS
    src = __import__("inspect").getsource(triage)
    flags_block = re.search(r"RED_FLAGS\s*=\s*\((.+?)\n\s*\)", src, re.S).group(1)
    segments: dict[str, list[str]] = {}
    current = None
    for line in flags_block.splitlines():
        m = re.match(r"\s*#\s*(English|Pidgin|Hausa|Yoruba|Igbo)(.*)", line)
        if m:
            current = m.group(1).lower()
            segments.setdefault(current, [])
            continue
        for s in re.findall(r'"([^"]*)"', line):
            segments.setdefault(current, []).append(s)
    _RED_FLAG_SEGMENTS = segments
    return segments


def string_items(language_name: str) -> list[dict]:
    """Every user-facing string for one language, in review order."""
    items = []
    lead = safety_net.RETURN_LEADS[language_name]
    items.append({
        "key": "return_lead", "label": "Return lead-in",
        "english": 'lead-in for "GO NOW IF"', "draft": lead,
    })
    for audience in ("child", "adult"):
        items.append({
            "key": f"return_signs_{audience}", "label": f"Return signs ({audience})",
            "english": "danger signs to watch after a non-emergency verdict",
            "draft": safety_net.RETURN_SIGNS[language_name][safety_net.audience(audience == "child")],
        })
    items.append({
        "key": "emergency_override", "label": "Emergency override",
        "english": "sent immediately when a danger sign is detected",
        "draft": triage.EMERGENCY_OVERRIDE_REPLIES[language_name].replace("\n", " "),
    })
    for i, phrase in enumerate(_red_flag_segments().get(language_name, [])):
        phrase = phrase.strip()
        items.append({
            "key": f"redflag_{i}", "label": "Red-flag phrase",
            "english": GLOSS.get(language_name, {}).get(phrase, ""),
            "draft": phrase,
        })
    return items


def vignette_items(language_name: str) -> list[dict]:
    rows = []
    with open("eval/vignettes.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    items = []
    for r in rows:
        if r["language"] != language_name:
            continue
        items.append({
            "key": r["id"],
            "label": f"Vignette {r['id']} — expected {r['expected'].replace('_', ' ').title()}",
            "english": "patient turns as scripted in the evaluation",
            "draft": r["messages"].replace(" || ", "\n").replace("| ", "\n"),
        })
    return items


def markers(language_name: str) -> dict:
    return {
        "words": sorted(language.WORD_MARKERS[language_name]),
        "phrases": sorted(language.PHRASE_MARKERS[language_name]),
    }


def items_for(language_name: str) -> list[dict]:
    """Full ordered review set for one language."""
    return string_items(language_name) + vignette_items(language_name)

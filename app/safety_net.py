"""Safety-netting: what to watch for, and when to come back.

Triage is a snapshot; illness moves. A child correctly sent home at
10am can deteriorate by evening, and the emergency-sensitivity figure —
measured at the moment of triage — would never see it. WHO IMCI
therefore treats "when to return immediately" as a required step with
named signs, not a vague "if it gets worse".

So every non-emergency verdict carries specific return signs. They are
built here rather than left to the model: this is the safety floor, and
it must not depend on the LLM choosing to mention it.

Signs differ by who is unwell — a young infant's danger signs are not
an adult's — following the IMCI age bands.

Hausa/Yoruba/Igbo are draft translations; have native speakers verify
them before deployment.
"""

# Audience keys. The USSD menu and the LLM both resolve to one of these.
CHILD = "child"
ADULT = "adult"

# Terse enough for a USSD screen (the whole END message must fit 182
# characters), and reused verbatim on WhatsApp so there is a single set
# of clinical strings to verify rather than two that could drift.
RETURN_SIGNS = {
    "english": {
        CHILD: "fits, cannot drink or feed, hard/fast breathing, blood in stool, or gets worse",
        ADULT: "chest pain, hard breathing, cannot wake, heavy bleeding, or gets worse",
    },
    "pidgin": {
        CHILD: "e shake body, e no fit drink, e dey breathe hard, blood dey for toilet, or e worse",
        ADULT: "chest dey pain, breathing hard, person no dey wake, plenty blood, or e worse",
    },
    "hausa": {
        CHILD: "farfadiya, ba ya sha, numfashi da wahala, jini a bayan gida, ko ya tsananta",
        ADULT: "ciwon kirji, wahalar numfashi, ba ya farkawa, jini mai yawa, ko ya tsananta",
    },
    "yoruba": {
        CHILD: "giri, ko le mu omi, emi lile, eje ninu igbe, tabi o buru si",
        ADULT: "irora aya, emi lile, ko le ji, eje pupo, tabi o buru si",
    },
    "igbo": {
        CHILD: "ọgbọ, ọ naghị aṅụ, iku ume ike, ọbara na nsi, ma ọ bụ ọ ka njọ",
        ADULT: "mgbu obi, iku ume ike, ọ naghị eteta, ọbara buru ibu, ma ọ bụ ọ ka njọ",
    },
}

# "Come back if…" lead-ins, sized for each channel.
RETURN_LEADS = {
    "english": "GO NOW IF:",
    "pidgin": "GO NOW IF:",
    "hausa": "JE YANZU IDAN:",
    "yoruba": "LO BAYII TI:",
    "igbo": "GAA UGBU A MA:",
}


def audience(is_child: bool) -> str:
    return CHILD if is_child else ADULT


def return_signs(language: str, is_child: bool) -> str:
    """The signs that mean seek care immediately."""
    signs = RETURN_SIGNS.get(language, RETURN_SIGNS["english"])
    return signs[audience(is_child)]


def advice_line(language: str, is_child: bool) -> str:
    """One-line safety net, for a USSD screen."""
    lead = RETURN_LEADS.get(language, RETURN_LEADS["english"])
    return f"{lead} {return_signs(language, is_child)}"


def advice_block(language: str, is_child: bool) -> str:
    """Safety net for WhatsApp, where there is room to set it apart."""
    return f"⚠️ {advice_line(language, is_child)}"

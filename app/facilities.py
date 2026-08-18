"""Facility routing (Sprint 6): nearest-facility lookup.

Haversine over all rows is fine at Nigerian facility-registry scale
(~40k rows); PostGIS can replace it behind the same interface later.
Emergencies prefer hospitals over PHCs even when a PHC is closer.
"""

import csv
import logging
from math import asin, cos, radians, sin, sqrt

from sqlalchemy import delete, select

from app import db
from app.models import Facility

log = logging.getLogger(__name__)

EMERGENCY_TYPES = {"GENERAL_HOSPITAL", "TEACHING_HOSPITAL"}

TYPE_LABELS = {
    "PHC": "Primary Health Centre",
    "GENERAL_HOSPITAL": "General Hospital",
    "TEACHING_HOSPITAL": "Teaching Hospital",
}

# Hausa/Yoruba/Igbo are draft translations — verify with native speakers.
NEAREST_LEADS = {
    "english": "Nearest health facility to you:",
    "pidgin": "The nearest place wey fit help you:",
    "hausa": "Asibitin da ya fi kusa da kai:",
    "yoruba": "Ile-iwosan to sunmo o julo:",
    "igbo": "Ụlọ ọgwụ kacha nso gị:",
}
NO_FACILITY_REPLIES = {
    "english": (
        "I could not find a facility near that location. Please go to the "
        "nearest clinic or hospital you know, or ask someone around you."
    ),
    "pidgin": (
        "I no fit find any facility near that place. Abeg go the nearest "
        "clinic or hospital wey you know, or ask person wey dey around."
    ),
    "hausa": (
        "Ban sami asibiti kusa da wurin ba. Don Allah je asibiti ko "
        "cibiyar lafiya da ka sani mafi kusa, ko tambayi mutanen kusa."
    ),
    "yoruba": (
        "Mi o ri ile-iwosan nitosi ibe. Jowo lo si ile-iwosan tabi "
        "ile-ise ilera ti o mo to sunmo, tabi beere lowo awon eniyan."
    ),
    "igbo": (
        "Ahụghị m ụlọ ọgwụ dị nso ebe ahụ. Biko gaa ụlọ ọgwụ ọ bụla ị "
        "maara nke kacha nso, ma ọ bụ jụọ ndị nọ gị nso."
    ),
}
DIRECTIONS_LABELS = {
    "english": "Directions",
    "pidgin": "Follow road go there",
    "hausa": "Hanyar zuwa",
    "yoruba": "Ọna to lọ sibẹ",
    "igbo": "Ụzọ ị ga-esi gaa",
}


def maps_link(facility) -> str:
    """Google Maps directions to the facility.

    Coordinates rather than the name: many Nigerian facilities are not
    searchable by name, and a wrong search result during an emergency
    is worse than no link. `dir` opens navigation from wherever the
    person is, which is what someone told to go now actually needs.
    """
    return (
        "https://www.google.com/maps/dir/?api=1&destination="
        f"{facility.latitude},{facility.longitude}"
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def find_nearest(
    latitude: float, longitude: float, emergency: bool = False
) -> tuple[Facility, float] | None:
    """(facility, distance_km) closest to the point, or None.

    Never raises — a broken database must not break a triage reply.
    """
    try:
        with db.get_session() as session:
            rows = session.scalars(select(Facility)).all()
    except Exception:
        log.warning("Facility lookup unavailable", exc_info=True)
        return None
    if not rows:
        return None
    if emergency:
        hospitals = [f for f in rows if f.facility_type in EMERGENCY_TYPES]
        rows = hospitals or rows
    best = min(rows, key=lambda f: haversine_km(latitude, longitude, f.latitude, f.longitude))
    return best, haversine_km(latitude, longitude, best.latitude, best.longitude)


def format_facility_reply(facility: Facility, distance_km: float, language: str) -> str:
    lead = NEAREST_LEADS.get(language, NEAREST_LEADS["english"])
    type_label = TYPE_LABELS.get(facility.facility_type, facility.facility_type)
    directions = DIRECTIONS_LABELS.get(language, DIRECTIONS_LABELS["english"])
    return (
        f"🏥 {lead}\n"
        f"{facility.name} — {type_label}\n"
        f"{facility.lga}, {facility.state}\n"
        f"≈ {distance_km:.1f} km from you\n\n"
        f"📍 {directions}: {maps_link(facility)}"
    )


def no_facility_reply(language: str) -> str:
    return NO_FACILITY_REPLIES.get(language, NO_FACILITY_REPLIES["english"])


def count_facilities() -> int:
    """Number of facility rows — used to decide whether to auto-seed."""
    from sqlalchemy import func

    with db.get_session() as session:
        return int(session.execute(select(func.count()).select_from(Facility)).scalar() or 0)


def seed_facilities(csv_path: str) -> int:
    """Replace the facilities table with the rows in the CSV."""
    db.init_db()
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [
            Facility(
                name=r["name"],
                facility_type=r["facility_type"],
                state=r["state"],
                lga=r["lga"],
                latitude=float(r["latitude"]),
                longitude=float(r["longitude"]),
            )
            for r in csv.DictReader(f)
        ]
    with db.get_session() as session:
        session.execute(delete(Facility))
        session.add_all(rows)
        session.commit()
    return len(rows)

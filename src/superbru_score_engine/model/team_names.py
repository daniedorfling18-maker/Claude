from __future__ import annotations

import re
import unicodedata


TEAM_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "america": "United States",
    "korea republic": "South Korea",
    "republic of korea": "South Korea",
    "south korea": "South Korea",
    "korea south": "South Korea",
    "ir iran": "Iran",
    "iran": "Iran",
    "czechia": "Czech Republic",
    "czech republic": "Czech Republic",
    "bosnia herzegovina": "Bosnia & Herzegovina",
    "bosnia and herzegovina": "Bosnia & Herzegovina",
    "bosnia herz": "Bosnia & Herzegovina",
    "dr congo": "DR Congo",
    "congo dr": "DR Congo",
    "democratic republic of congo": "DR Congo",
    "congo democratic republic": "DR Congo",
    "ivory coast": "Ivory Coast",
    "cote d ivoire": "Ivory Coast",
    "cotedivoire": "Ivory Coast",
    "cape verde": "Cape Verde",
    "cabo verde": "Cape Verde",
    "curacao": "Curacao",
    "curaao": "Curacao",
    "curaaoo": "Curacao",
    "curaaao": "Curacao",
    "turkiye": "Turkey",
    "turkey": "Turkey",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
    "korea dpr": "Korea DPR",
    "north korea": "Korea DPR",
}


def team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_team_name(name: str) -> str:
    stripped = str(name or "").strip()
    key = team_key(stripped)
    return TEAM_ALIASES.get(key, stripped)


def canonical_team_key(name: str) -> str:
    return team_key(canonical_team_name(name))

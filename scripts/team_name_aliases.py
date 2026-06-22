"""Team-name alias canonicalization for SuperBru automation.

The automation needs to join names from multiple sources:
- the committed locked-card CSV;
- SuperBru's in-browser labels;
- The Odds API event names.

This module normalizes common country/team variants to one canonical key so
names such as "United States", "USA", and "US" match reliably.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


TEAM_ALIASES: dict[str, set[str]] = {
    "algeria": {"algeria", "alg", "dza"},
    "argentina": {"argentina", "arg"},
    "australia": {"australia", "aus"},
    "austria": {"austria", "aut"},
    "belgium": {"belgium", "bel"},
    "bosniaherzegovina": {
        "bosniaandherzegovina",
        "bosniaherzegovina",
        "bosnia",
        "bih",
        "bhi",
    },
    "brazil": {"brazil", "bra"},
    "canada": {"canada", "can"},
    "capeverde": {"capeverde", "caboverde", "cpv"},
    "colombia": {"colombia", "col"},
    "croatia": {"croatia", "cro"},
    "curacao": {"curacao", "curacao", "cur", "cuw"},
    "czechia": {"czechia", "czechrepublic", "cze"},
    "drcongo": {
        "drcongo",
        "drc",
        "cod",
        "congodr",
        "democraticrepublicofthecongo",
        "demrepcongo",
    },
    "ecuador": {"ecuador", "ecu"},
    "egypt": {"egypt", "egy"},
    "england": {"england", "eng"},
    "france": {"france", "fra"},
    "germany": {"germany", "ger", "deutschland"},
    "ghana": {"ghana", "gha"},
    "haiti": {"haiti", "hti", "hai"},
    "iran": {"iran", "iriran", "iri", "irn"},
    "iraq": {"iraq", "irq"},
    "ivorycoast": {"ivorycoast", "cotedivoire", "civ", "ci"},
    "japan": {"japan", "jpn"},
    "jordan": {"jordan", "jor"},
    "mexico": {"mexico", "mex"},
    "morocco": {"morocco", "mar"},
    "netherlands": {"netherlands", "holland", "ned", "nld"},
    "newzealand": {"newzealand", "nzl"},
    "norway": {"norway", "nor"},
    "panama": {"panama", "pan"},
    "paraguay": {"paraguay", "par"},
    "portugal": {"portugal", "por"},
    "qatar": {"qatar", "qat"},
    "saudiarabia": {"saudiarabia", "ksa", "sau"},
    "scotland": {"scotland", "sco"},
    "senegal": {"senegal", "sen"},
    "southafrica": {"southafrica", "rsa", "zaf"},
    "southkorea": {"southkorea", "korearepublic", "korea", "kor"},
    "spain": {"spain", "esp"},
    "sweden": {"sweden", "swe"},
    "switzerland": {"switzerland", "sui", "che"},
    "tunisia": {"tunisia", "tun"},
    "turkey": {"turkey", "turkiye", "tur"},
    "unitedstates": {"unitedstates", "unitedstatesofamerica", "usa", "us", "america"},
    "uruguay": {"uruguay", "uru"},
    "uzbekistan": {"uzbekistan", "uzb"},
}

_NORMALIZED_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in TEAM_ALIASES.items():
    _NORMALIZED_TO_CANONICAL[_norm(canonical)] = _norm(canonical)
    for alias in aliases:
        _NORMALIZED_TO_CANONICAL[_norm(alias)] = _norm(canonical)


def canonical_team_key(value: Any) -> str:
    """Return a canonical key for a team name or alias."""
    normalized = _norm(value)
    return _NORMALIZED_TO_CANONICAL.get(normalized, normalized)


def team_alias_keys(value: Any) -> set[str]:
    """Return normalized aliases that should match this team in browser text."""
    canonical = canonical_team_key(value)
    aliases = TEAM_ALIASES.get(canonical, {canonical})
    return {canonical, _norm(value), *{_norm(alias) for alias in aliases}}


def same_team(left: Any, right: Any) -> bool:
    return canonical_team_key(left) == canonical_team_key(right)

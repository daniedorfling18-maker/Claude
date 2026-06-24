"""Bracket Monte Carlo for the in-progress 2026 World Cup (48 teams).

Derives the 12 four-team groups from the group-stage fixture list (each group is a
round-robin, so the match graph is twelve disjoint 4-cliques), applies completed results
as the current standings, then repeatedly simulates the remaining group matches and a
reseeded knockout to estimate each team's championship probability.

Match outcomes come from team strength ratings via a Poisson goal model (consistent with
the score engine): a rating gap becomes a goal supremacy, goals are Poisson, and the
score gives win/draw/loss plus a goal difference for group tiebreaks. Because a champion
must win ~5 knockout ties, this concentrates probability onto the strong teams the way
real outright-winner odds do - unlike a single-shot strength softmax, which is far too flat.

The knockout is reseeded (random redraw each round) because the exact bracket for an
in-progress tournament is not yet determined; this is an explicit, documented approximation.
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

ALIASES = {
    "czechia": "czech republic", "ir iran": "iran", "korea republic": "south korea",
    "bosnia and herzegovina": "bosnia herzegovina", "congo dr": "dr congo",
    "cote d ivoire": "ivory coast", "cabo verde": "cape verde", "turkiye": "turkey",
    "usa": "united states", "usmnt": "united states",
}


def norm(name) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).strip()
    return ALIASES.get(text, text)


@dataclass
class GroupStage:
    groups: dict[str, list[str]]              # group_id -> 4 team keys
    teams: set[str]                           # all team keys
    standings: dict[str, dict]                # team_key -> {pts, gd, played} from completed results
    remaining: list[tuple[str, str]]          # (team_key, team_key) group matches not yet played
    warnings: list[str] = field(default_factory=list)


def _connected_components(edges):
    adj = defaultdict(set)
    nodes = set()
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
        nodes.add(a)
        nodes.add(b)
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack.extend(adj[x] - seen)
        comps.append(comp)
    return comps


def derive_group_stage(fixtures, results, normalize=norm) -> GroupStage:
    """fixtures: iterable of (home, away) for ALL group matches. results: iterable of
    (home, away, home_goals, away_goals) for COMPLETED matches. Names are normalised with
    ``normalize`` (pass the caller's normaliser so keys match its ratings/tokens)."""
    norm = normalize
    fpairs = [(norm(h), norm(a)) for h, a in fixtures if norm(h) and norm(a) and norm(h) != norm(a)]
    comps = _connected_components(fpairs)
    warnings = []
    groups = {}
    for i, comp in enumerate(sorted(comps, key=lambda c: sorted(c))):
        gid = chr(ord("A") + i) if i < 26 else f"G{i}"
        groups[gid] = sorted(comp)
        if len(comp) != 4:
            warnings.append(f"group {gid} has {len(comp)} teams (expected 4)")
    teams = {t for comp in comps for t in comp}

    standings = {t: {"pts": 0.0, "gd": 0, "played": 0} for t in teams}
    played_pairs = set()
    for h, a, hg, ag in results:
        hk, ak = norm(h), norm(a)
        if hk not in teams or ak not in teams:
            continue
        pair = frozenset((hk, ak))
        if pair in played_pairs:
            continue
        played_pairs.add(pair)
        hg, ag = int(hg), int(ag)
        standings[hk]["played"] += 1
        standings[ak]["played"] += 1
        standings[hk]["gd"] += hg - ag
        standings[ak]["gd"] += ag - hg
        if hg > ag:
            standings[hk]["pts"] += 3
        elif ag > hg:
            standings[ak]["pts"] += 3
        else:
            standings[hk]["pts"] += 1
            standings[ak]["pts"] += 1

    remaining = [(h, a) for (h, a) in fpairs if frozenset((h, a)) not in played_pairs]
    return GroupStage(groups=groups, teams=teams, standings=standings, remaining=remaining, warnings=warnings)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _poisson(rng: random.Random, lam: float) -> int:
    lam = max(0.01, lam)
    target = math.exp(-lam)
    k, prod = 0, 1.0
    while True:
        k += 1
        prod *= rng.random()
        if prod <= target:
            return k - 1


def _sim_goals(rng, ra, rb, total_goals, scale):
    sup = max(-3.0, min(3.0, scale * (ra - rb)))
    la = max(0.15, total_goals / 2.0 + sup / 2.0)
    lb = max(0.15, total_goals / 2.0 - sup / 2.0)
    return _poisson(rng, la), _poisson(rng, lb)


def simulate_winner_probabilities(stage: GroupStage, ratings: dict[str, float], *, n_sims: int = 20000,
                                  seed: int = 20260624, total_goals: float = 2.6, scale: float = 1.0,
                                  best_thirds: int = 8) -> dict[str, float]:
    """Per-team championship probability from n_sims tournament simulations."""
    rng = random.Random(seed)
    teams = sorted(stage.teams)

    def R(t):
        return ratings.get(t, 0.0)

    champions = defaultdict(int)
    for _ in range(n_sims):
        pts = {t: stage.standings[t]["pts"] for t in teams}
        gd = {t: float(stage.standings[t]["gd"]) for t in teams}
        for h, a in stage.remaining:
            gh, ga = _sim_goals(rng, R(h), R(a), total_goals, scale)
            gd[h] += gh - ga
            gd[a] += ga - gh
            if gh > ga:
                pts[h] += 3
            elif ga > gh:
                pts[a] += 3
            else:
                pts[h] += 1
                pts[a] += 1

        firsts, seconds, thirds = [], [], []
        for members in stage.groups.values():
            ranked = sorted(members, key=lambda t: (pts[t], gd[t], R(t), rng.random()), reverse=True)
            if len(ranked) >= 1:
                firsts.append(ranked[0])
            if len(ranked) >= 2:
                seconds.append(ranked[1])
            if len(ranked) >= 3:
                thirds.append(ranked[2])
        thirds.sort(key=lambda t: (pts[t], gd[t], R(t), rng.random()), reverse=True)
        alive = firsts + seconds + thirds[:best_thirds]

        # Reseeded knockout: random redraw each round until one champion remains.
        while len(alive) > 1:
            if len(alive) % 2 == 1:  # safety: drop the weakest if an odd field ever occurs
                alive.sort(key=lambda t: (R(t), rng.random()))
                alive = alive[1:]
            rng.shuffle(alive)
            nxt = []
            for i in range(0, len(alive), 2):
                A, B = alive[i], alive[i + 1]
                gh, ga = _sim_goals(rng, R(A), R(B), total_goals, scale)
                if gh > ga:
                    nxt.append(A)
                elif ga > gh:
                    nxt.append(B)
                else:  # extra time / penalties: stronger side slightly favoured
                    nxt.append(A if rng.random() < _sigmoid(R(A) - R(B)) else B)
            alive = nxt
        if alive:
            champions[alive[0]] += 1

    return {t: champions[t] / n_sims for t in teams}

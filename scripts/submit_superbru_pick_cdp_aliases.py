"""Alias-aware SuperBru submitter wrapper.

This delegates to `submit_superbru_pick_cdp.py` and replaces its in-browser
match finders with safer alias-aware versions. A SuperBru row or tab must match
both teams before it is selected, and short aliases are token-matched instead of
blind substring-matched.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from team_name_aliases import TEAM_ALIASES


def load_submitter_module():
    submitter_path = Path(__file__).with_name("submit_superbru_pick_cdp.py")
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp", submitter_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load submitter module: {submitter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


submitter = load_submitter_module()
ORIGINAL_PICK_SCORE_INPUTS = submitter.pick_score_inputs
ALIASES_JSON = json.dumps({key: sorted(values) for key, values in TEAM_ALIASES.items()}, sort_keys=True)


def _team_js_body(find_row: bool) -> str:
    text_expr = "el.innerText || el.textContent || ''" if find_row else "el.getAttribute('data-brutip') || ''"
    prefix = "" if find_row else "const controls = Array.from(document.querySelectorAll('[data-brutip][data-bru-tab]'));\n  "
    iterator = "Array.from(document.querySelectorAll('tr, li, div, section, article')).filter(el => el.querySelector('input, select'))" if find_row else "controls"
    if find_row:
        success = """
      if (!matchRow || (el.innerText || '').length < (matchRow.innerText || '').length) {
        matchRow = el;
      }"""
        after_loop = """
  if (!matchRow) {
    return {found: false, reason: 'no input-bearing element contains both team names or safe aliases', homeVariants, awayVariants};
  }
  const inputs = Array.from(matchRow.querySelectorAll('input, select')).map(el => {
    // Document-unique selector: a bare class like .soccer-left-score matches the
    // FIRST such input in the DOM, which is a DIFFERENT game whenever several
    // pickers share the page (2026-07-08: Spain-Belgium filled France-Morocco's
    // inputs, game 97 vs 98, so the real pick never reached the server).
    const gameNode = el.closest('[data-bru-game-id]');
    const gameId = gameNode ? String(gameNode.getAttribute('data-bru-game-id')) : '';
    let docSelector = '';
    const cls = el.className || '';
    if (gameId && /soccer-(left|right)-score/.test(cls)) {
      const side = cls.includes('soccer-left-score') ? 'left' : 'right';
      const candidate = '[data-bru-game-id="' + gameId + '"] input.soccer-' + side + '-score';
      if (document.querySelectorAll(candidate).length === 1) docSelector = candidate;
    }
    return {
      tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
      className: cls, placeholder: el.placeholder || '', value: el.value || '',
      visible: el.offsetParent !== null, maxlength: el.maxLength || '',
      gameId, docSelector
    };
  });
  const buttons = Array.from(matchRow.querySelectorAll('button, input[type=submit], a[class*=save], a[class*=submit]')).map(b => ({
    tag: b.tagName.toLowerCase(), type: b.type || '', text: (b.innerText || b.value || b.textContent || '').trim().slice(0, 80),
    id: b.id || '', className: b.className || ''
  }));
  return {
    found: true,
    rowTag: matchRow.tagName,
    rowId: matchRow.id || '',
    rowClass: matchRow.className || '',
    rowText: (matchRow.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
    matchedHomeAliases: homeVariants,
    matchedAwayAliases: awayVariants,
    inputs,
    buttons
  };"""
    else:
        success = """
      el.click();
      return el.getAttribute('data-bru-tab') + ': ' + el.getAttribute('data-brutip');"""
        after_loop = """
  return null;"""

    return f"""
([homeTeam, awayTeam]) => {{
  const TEAM_ALIASES = {ALIASES_JSON};
  function ascii(s) {{
    return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }}
  function compact(s) {{
    return ascii(s).replace(/[^a-z0-9]/g, '');
  }}
  function tokens(s) {{
    return new Set(ascii(s).split(/[^a-z0-9]+/).filter(Boolean));
  }}
  function variants(team) {{
    const n = compact(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {{
      if (canonical === n || aliases.includes(n)) {{
        return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
      }}
    }}
    return [n].filter(Boolean);
  }}
  function containsTeam(rawText, candidates) {{
    const c = compact(rawText);
    const t = tokens(rawText);
    return candidates.some(candidate => {{
      if (!candidate) return false;
      if (t.has(candidate) || c === candidate) return true;
      return candidate.length > 3 && c.includes(candidate);
    }});
  }}
  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);
  {prefix}let matchRow = null;
  for (const el of {iterator}) {{
    const raw = {text_expr};
    const c = compact(raw);
    if (containsTeam(raw, homeVariants) && containsTeam(raw, awayVariants) && c.length < 2000) {{{success}
    }}
  }}{after_loop}
}}
"""


CLICK_ALL_ROUNDS_JS = f"""
async ([homeTeam, awayTeam]) => {{
  const TEAM_ALIASES = {ALIASES_JSON};
  function wait(ms) {{ return new Promise(resolve => setTimeout(resolve, ms)); }}
  function ascii(s) {{ return (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase(); }}
  function compact(s) {{ return ascii(s).replace(/[^a-z0-9]/g, ''); }}
  function tokens(s) {{ return new Set(ascii(s).split(/[^a-z0-9]+/).filter(Boolean)); }}
  function variants(team) {{
    const n = compact(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {{
      if (canonical === n || aliases.includes(n)) return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
    }}
    return [n].filter(Boolean);
  }}
  function containsTeam(rawText, candidates) {{
    const c = compact(rawText);
    const t = tokens(rawText);
    return candidates.some(candidate => {{
      if (!candidate) return false;
      if (t.has(candidate) || c === candidate) return true;
      return candidate.length > 3 && c.includes(candidate);
    }});
  }}
  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);
  function clickMatchingGame() {{
    const controls = Array.from(document.querySelectorAll('[data-brutip][data-bru-tab]'));
    for (const el of controls) {{
      const raw = el.getAttribute('data-brutip') || '';
      if (containsTeam(raw, homeVariants) && containsTeam(raw, awayVariants)) {{
        el.click();
        return el.getAttribute('data-bru-tab') + ': ' + raw;
      }}
    }}
    return null;
  }}
  const immediate = clickMatchingGame();
  if (immediate) return immediate;
  const rounds = Array.from(document.querySelectorAll('[data-bru-tab^="round"]'))
    .filter(el => !(el.getAttribute('data-brutip') || '').includes(' v '));
  let activeIndex = rounds.findIndex(el =>
    (el.className || '').toLowerCase().includes('active') || el.getAttribute('aria-selected') === 'true'
  );
  if (activeIndex < 0) {{
    activeIndex = rounds.findIndex(el => el.getAttribute('data-bru-tab') === 'round3');
  }}
  const start = activeIndex >= 0 ? activeIndex + 1 : 0;
  for (let step = 0; step < rounds.length; step++) {{
    const i = (start + step) % rounds.length;
    const round = rounds[i];
    const rawRoundText = (round.innerText || round.textContent || '').replace(/\\s+/g, ' ').trim();
    round.click();
    await wait(3000);
    const match = clickMatchingGame();
    if (match) return 'round:' + round.getAttribute('data-bru-tab') + ': ' + rawRoundText + ' -> ' + match;
  }}
  return null;
}}
"""


def pick_score_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer SuperBru's explicit left/right score classes before generic inputs."""
    visible = [inp for inp in inputs if inp.get("visible")]
    left = next((inp for inp in visible if "soccer-left-score" in str(inp.get("className", ""))), None)
    right = next((inp for inp in visible if "soccer-right-score" in str(inp.get("className", ""))), None)
    if left and right:
        return [left, right]
    return ORIGINAL_PICK_SCORE_INPUTS(inputs)


SET_GOAL_JS = r"""
([selector, value]) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'element not found: ' + selector };
  const side = (el.className || '').includes('soccer-left-score') ? 'left'
    : (el.className || '').includes('soccer-right-score') ? 'right'
    : '';
  const gameNode = el.closest('[data-bru-game-id]');
  const gameId = gameNode ? parseInt(gameNode.getAttribute('data-bru-game-id'), 10) : NaN;
  const goal = parseInt(value, 10);
  if (!side || !Number.isFinite(gameId) || !Number.isFinite(goal)) {
    return { ok: false, reason: 'could not resolve side/gameId/goal', selector, side, gameId, value };
  }
  if (!window.brupicks || !brupicks.soccer || typeof brupicks.soccer.setGoal !== 'function') {
    return { ok: false, reason: 'brupicks.soccer.setGoal unavailable', selector, side, gameId, value };
  }
  brupicks.soccer.setGoal(gameId, side, goal);
  const newValue = el.value || '';
  return { ok: String(newValue) === String(goal), selector, side, gameId, value: String(goal), newValue };
}
"""


submitter.FIND_ROW_JS = _team_js_body(find_row=True)
submitter.CLICK_SUBTAB_JS = CLICK_ALL_ROUNDS_JS
submitter.SET_INPUT_JS = SET_GOAL_JS
submitter.pick_score_inputs = pick_score_inputs

build_parser = submitter.build_parser
run = submitter.run


def main() -> int:
    return submitter.main()


if __name__ == "__main__":
    raise SystemExit(main())

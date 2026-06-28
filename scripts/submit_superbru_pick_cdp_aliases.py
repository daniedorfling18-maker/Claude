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
    iterator = "Array.from(document.querySelectorAll('tr, li, div, section, article'))" if find_row else "controls"
    if find_row:
        success = """
      if (!matchRow || (el.innerText || '').length < (matchRow.innerText || '').length) {
        matchRow = el;
      }"""
        after_loop = """
  if (!matchRow) {
    return {found: false, reason: 'no element contains both team names or safe aliases', homeVariants, awayVariants};
  }
  const inputs = Array.from(matchRow.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
    className: el.className || '', placeholder: el.placeholder || '', value: el.value || '',
    visible: el.offsetParent !== null, maxlength: el.maxLength || ''
  }));
  const buttons = Array.from(matchRow.querySelectorAll('button, input[type=submit], a[class*=save], a[class*=submit]')).map(b => ({
    tag: b.tagName.toLowerCase(), type: b.type || '', text: (b.innerText || b.value || b.textContent || '').trim().slice(0, 80),
    id: b.id || '', className: b.className || ''
  }));
  return {
    found: true,
    rowTag: matchRow.tagName,
    rowId: matchRow.id || '',
    rowClass: matchRow.className || '',
    rowText: (matchRow.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300),
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


def pick_score_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer SuperBru's explicit left/right score classes before generic inputs."""
    visible = [inp for inp in inputs if inp.get("visible")]
    left = next((inp for inp in visible if "soccer-left-score" in str(inp.get("className", ""))), None)
    right = next((inp for inp in visible if "soccer-right-score" in str(inp.get("className", ""))), None)
    if left and right:
        return [left, right]
    return ORIGINAL_PICK_SCORE_INPUTS(inputs)


submitter.FIND_ROW_JS = _team_js_body(find_row=True)
submitter.CLICK_SUBTAB_JS = _team_js_body(find_row=False)
submitter.pick_score_inputs = pick_score_inputs

build_parser = submitter.build_parser
run = submitter.run


def main() -> int:
    return submitter.main()


if __name__ == "__main__":
    raise SystemExit(main())

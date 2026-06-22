"""Alias-aware SuperBru submitter wrapper.

This delegates to `submit_superbru_pick_cdp.py` but replaces its in-browser
match finders with alias-aware versions. It allows canonical names from the
locked card, such as "United States", to match SuperBru labels such as "USA".
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load_submitter_module():
    submitter_path = Path(__file__).with_name("submit_superbru_pick_cdp.py")
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp", submitter_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load submitter module: {submitter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


submitter = load_submitter_module()

# JS: find a match row by canonical team name or any known alias, then locate score inputs.
FIND_ROW_JS = r"""
([homeTeam, awayTeam]) => {
  const TEAM_ALIASES = {
    algeria: ['algeria','alg','dza'],
    argentina: ['argentina','arg'],
    australia: ['australia','aus'],
    austria: ['austria','aut'],
    belgium: ['belgium','bel'],
    bosniaherzegovina: ['bosniaandherzegovina','bosniaherzegovina','bosnia','bih','bhi'],
    brazil: ['brazil','bra'],
    canada: ['canada','can'],
    capeverde: ['capeverde','caboverde','cpv'],
    colombia: ['colombia','col'],
    croatia: ['croatia','cro'],
    curacao: ['curacao','cur','cuw'],
    czechia: ['czechia','czechrepublic','cze'],
    drcongo: ['drcongo','drc','cod','congodr','democraticrepublicofthecongo','demrepcongo'],
    ecuador: ['ecuador','ecu'],
    egypt: ['egypt','egy'],
    england: ['england','eng'],
    france: ['france','fra'],
    germany: ['germany','ger','deutschland'],
    ghana: ['ghana','gha'],
    haiti: ['haiti','hti','hai'],
    iran: ['iran','iriran','iri','irn'],
    iraq: ['iraq','irq'],
    ivorycoast: ['ivorycoast','cotedivoire','civ','ci'],
    japan: ['japan','jpn'],
    jordan: ['jordan','jor'],
    mexico: ['mexico','mex'],
    morocco: ['morocco','mar'],
    netherlands: ['netherlands','holland','ned','nld'],
    newzealand: ['newzealand','nzl'],
    norway: ['norway','nor'],
    panama: ['panama','pan'],
    paraguay: ['paraguay','par'],
    portugal: ['portugal','por'],
    qatar: ['qatar','qat'],
    saudiarabia: ['saudiarabia','ksa','sau'],
    scotland: ['scotland','sco'],
    senegal: ['senegal','sen'],
    southafrica: ['southafrica','rsa','zaf'],
    southkorea: ['southkorea','korearepublic','korea','kor'],
    spain: ['spain','esp'],
    sweden: ['sweden','swe'],
    switzerland: ['switzerland','sui','che'],
    tunisia: ['tunisia','tun'],
    turkey: ['turkey','turkiye','tur'],
    unitedstates: ['unitedstates','unitedstatesofamerica','usa','us','america'],
    uruguay: ['uruguay','uru'],
    uzbekistan: ['uzbekistan','uzb'],
  };
  function norm(s) {
    return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function variants(team) {
    const n = norm(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {
      if (canonical === n || aliases.includes(n)) {
        return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
      }
    }
    return [n].filter(Boolean);
  }
  function containsAny(text, candidates) {
    return candidates.some(candidate => candidate && text.includes(candidate));
  }

  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);

  const all = Array.from(document.querySelectorAll('tr, li, div, section, article'));
  let matchRow = null;
  for (const el of all) {
    const t = norm(el.innerText || el.textContent || '');
    if (containsAny(t, homeVariants) && containsAny(t, awayVariants) && t.length < 2000) {
      if (!matchRow || (el.innerText || '').length < (matchRow.innerText || '').length) {
        matchRow = el;
      }
    }
  }
  if (!matchRow) {
    return {
      found: false,
      reason: 'no element contains both team names or aliases',
      homeVariants,
      awayVariants,
    };
  }

  const inputs = Array.from(matchRow.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    className: el.className || '',
    placeholder: el.placeholder || '',
    value: el.value || '',
    visible: el.offsetParent !== null,
    maxlength: el.maxLength || ''
  }));

  const buttons = Array.from(matchRow.querySelectorAll('button, input[type=submit], a[class*=save], a[class*=submit]')).map(b => ({
    tag: b.tagName.toLowerCase(),
    type: b.type || '',
    text: (b.innerText || b.value || b.textContent || '').trim().slice(0, 80),
    id: b.id || '',
    className: b.className || ''
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
  };
}
"""

# JS: click the subtab using canonical team name or aliases before finding the row.
CLICK_SUBTAB_JS = r"""
([homeTeam, awayTeam]) => {
  const TEAM_ALIASES = {
    algeria: ['algeria','alg','dza'], argentina: ['argentina','arg'], australia: ['australia','aus'], austria: ['austria','aut'],
    belgium: ['belgium','bel'], bosniaherzegovina: ['bosniaandherzegovina','bosniaherzegovina','bosnia','bih','bhi'], brazil: ['brazil','bra'], canada: ['canada','can'],
    capeverde: ['capeverde','caboverde','cpv'], colombia: ['colombia','col'], croatia: ['croatia','cro'], curacao: ['curacao','cur','cuw'],
    czechia: ['czechia','czechrepublic','cze'], drcongo: ['drcongo','drc','cod','congodr','democraticrepublicofthecongo','demrepcongo'], ecuador: ['ecuador','ecu'], egypt: ['egypt','egy'],
    england: ['england','eng'], france: ['france','fra'], germany: ['germany','ger','deutschland'], ghana: ['ghana','gha'], haiti: ['haiti','hti','hai'],
    iran: ['iran','iriran','iri','irn'], iraq: ['iraq','irq'], ivorycoast: ['ivorycoast','cotedivoire','civ','ci'], japan: ['japan','jpn'], jordan: ['jordan','jor'],
    mexico: ['mexico','mex'], morocco: ['morocco','mar'], netherlands: ['netherlands','holland','ned','nld'], newzealand: ['newzealand','nzl'], norway: ['norway','nor'],
    panama: ['panama','pan'], paraguay: ['paraguay','par'], portugal: ['portugal','por'], qatar: ['qatar','qat'], saudiarabia: ['saudiarabia','ksa','sau'],
    scotland: ['scotland','sco'], senegal: ['senegal','sen'], southafrica: ['southafrica','rsa','zaf'], southkorea: ['southkorea','korearepublic','korea','kor'],
    spain: ['spain','esp'], sweden: ['sweden','swe'], switzerland: ['switzerland','sui','che'], tunisia: ['tunisia','tun'], turkey: ['turkey','turkiye','tur'],
    unitedstates: ['unitedstates','unitedstatesofamerica','usa','us','america'], uruguay: ['uruguay','uru'], uzbekistan: ['uzbekistan','uzb'],
  };
  function norm(s) {
    return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function variants(team) {
    const n = norm(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {
      if (canonical === n || aliases.includes(n)) return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
    }
    return [n].filter(Boolean);
  }
  function containsAny(text, candidates) {
    return candidates.some(candidate => candidate && text.includes(candidate));
  }

  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);
  const controls = Array.from(document.querySelectorAll('[data-brutip][data-bru-tab]'));

  for (const c of controls) {
    const tip = norm(c.getAttribute('data-brutip') || '');
    if (containsAny(tip, homeVariants) && containsAny(tip, awayVariants)) {
      c.click();
      return c.getAttribute('data-bru-tab') + ': ' + c.getAttribute('data-brutip');
    }
  }

  for (const c of controls) {
    const tip = norm(c.getAttribute('data-brutip') || '');
    if (containsAny(tip, homeVariants) || containsAny(tip, awayVariants)) {
      c.click();
      return c.getAttribute('data-bru-tab') + ': ' + c.getAttribute('data-brutip');
    }
  }
  return null;
}
"""

submitter.FIND_ROW_JS = FIND_ROW_JS
submitter.CLICK_SUBTAB_JS = CLICK_SUBTAB_JS

build_parser = submitter.build_parser
run = submitter.run


def main() -> int:
    return submitter.main()


if __name__ == "__main__":
    raise SystemExit(main())

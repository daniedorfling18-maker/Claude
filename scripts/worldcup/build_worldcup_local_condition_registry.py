from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from datetime import datetime, timezone

OUT = Path("outputs/polymarket_model_governance/worldcup_local_condition_registry.csv")
SUMMARY = Path("outputs/polymarket_model_governance/worldcup_local_condition_registry_summary.json")

SEARCH_ROOTS = [
    Path("."),
    Path("outputs"),
]

INCLUDE_NAME_PATTERNS = [
    "england",
    "ghana",
    "completed_game",
    "fixture_moneyline",
    "worldcup",
]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def read_csv(path: Path):
    try:
        if not path.exists() or path.stat().st_size == 0:
            return []
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def parse_jsonish(x):
    if isinstance(x, list):
        return x
    if x is None:
        return []
    try:
        v = json.loads(x)
        if isinstance(v, list):
            return v
        return []
    except Exception:
        return []

def find_condition_ids_in_text(text: str):
    return sorted(set(re.findall(r"0x[a-fA-F0-9]{64}", text or "")))

def maybe_relevant_file(path: Path):
    name = path.name.lower()
    if path.suffix.lower() not in {".csv", ".json", ".txt", ".py"}:
        return False
    return any(p in name for p in INCLUDE_NAME_PATTERNS)

def get_first(row, names):
    for n in names:
        if n in row and str(row.get(n, "")).strip():
            return str(row.get(n, "")).strip()
    return ""

records = {}

files = []
for root in SEARCH_ROOTS:
    if root.exists():
        files.extend([p for p in root.rglob("*") if p.is_file() and maybe_relevant_file(p)])

for path in sorted(set(files)):
    if path.suffix.lower() == ".csv":
        rows = read_csv(path)
        for row in rows:
            condition_id = get_first(row, ["condition_id", "conditionId", "condition"])
            question = get_first(row, ["question", "title"])
            fixture_slug = get_first(row, ["fixture_slug", "slug"])
            fixture = get_first(row, ["fixture"])
            market_slug = get_first(row, ["market_slug", "slug"])
            market_index = get_first(row, ["market_index"])
            market_type = get_first(row, ["market_type"])
            team_or_draw = get_first(row, ["team_or_draw", "outcome_name"])
            token_id = get_first(row, ["token_id"])

            token_array = (
                parse_jsonish(get_first(row, ["clob_token_ids_raw", "clobTokenIds", "tokens_raw"]))
                or []
            )

            # Fallback: scrape condition ids from row values.
            if not condition_id:
                condition_ids = find_condition_ids_in_text(json.dumps(row, ensure_ascii=False))
            else:
                condition_ids = [condition_id]

            for cid in condition_ids:
                key = cid
                rec = records.setdefault(key, {
                    "discovered_at_utc": now_iso(),
                    "condition_id": cid,
                    "question": "",
                    "fixture_slug": "",
                    "fixture": "",
                    "market_slug": "",
                    "market_index": "",
                    "market_type": "",
                    "team_or_draw": "",
                    "token_ids_raw": "",
                    "single_token_id": "",
                    "source_files": set(),
                })

                if question and not rec["question"]:
                    rec["question"] = question
                if fixture_slug and not rec["fixture_slug"]:
                    rec["fixture_slug"] = fixture_slug
                if fixture and not rec["fixture"]:
                    rec["fixture"] = fixture
                if market_slug and not rec["market_slug"]:
                    rec["market_slug"] = market_slug
                if market_index and not rec["market_index"]:
                    rec["market_index"] = market_index
                if market_type and not rec["market_type"]:
                    rec["market_type"] = market_type
                if team_or_draw and not rec["team_or_draw"]:
                    rec["team_or_draw"] = team_or_draw
                if token_id and not rec["single_token_id"]:
                    rec["single_token_id"] = token_id
                if token_array and not rec["token_ids_raw"]:
                    rec["token_ids_raw"] = json.dumps(token_array, ensure_ascii=False)

                rec["source_files"].add(str(path))

    else:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue

        for cid in find_condition_ids_in_text(text):
            rec = records.setdefault(cid, {
                "discovered_at_utc": now_iso(),
                "condition_id": cid,
                "question": "",
                "fixture_slug": "",
                "fixture": "",
                "market_slug": "",
                "market_index": "",
                "market_type": "",
                "team_or_draw": "",
                "token_ids_raw": "",
                "single_token_id": "",
                "source_files": set(),
            })
            rec["source_files"].add(str(path))

out = []
for rec in records.values():
    rec = dict(rec)
    rec["source_files"] = "|".join(sorted(rec["source_files"]))
    out.append(rec)

out.sort(key=lambda r: (r.get("fixture_slug", ""), r.get("market_index", ""), r.get("question", ""), r["condition_id"]))

OUT.parent.mkdir(parents=True, exist_ok=True)

fieldnames = [
    "discovered_at_utc",
    "condition_id",
    "question",
    "fixture_slug",
    "fixture",
    "market_slug",
    "market_index",
    "market_type",
    "team_or_draw",
    "token_ids_raw",
    "single_token_id",
    "source_files",
]

with OUT.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out)

summary = {
    "files_scanned": len(files),
    "conditions_found": len(out),
    "conditions_with_questions": len([r for r in out if r["question"]]),
    "conditions_with_token_arrays": len([r for r in out if r["token_ids_raw"]]),
    "output": str(OUT),
}

SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))

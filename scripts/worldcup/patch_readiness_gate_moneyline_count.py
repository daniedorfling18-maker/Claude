from pathlib import Path

path = Path("build_worldcup_model_readiness_gate.py")
text = path.read_text(encoding="utf-8-sig")

old = '''moneyline_validation_rows = []
for path in MONEYLINE_VALIDATION_FILES:
    moneyline_validation_rows.extend(read_csv(path))
'''

new = '''moneyline_validation_rows = []
for moneyline_path in MONEYLINE_VALIDATION_FILES:
    for row in read_csv(moneyline_path):
        row["_source_file"] = str(moneyline_path)
        row["_source_stem"] = moneyline_path.stem
        moneyline_validation_rows.append(row)
'''

if old not in text:
    print("First block not found exactly; applying safer fallback patch.")
else:
    text = text.replace(old, new)

old2 = '''moneyline_games = unique_count(moneyline_validation_rows, "fixture_slug")'''

new2 = '''moneyline_games = len({
    (
        r.get("fixture_slug")
        or r.get("fixture")
        or r.get("_source_stem")
        or ""
    ).strip()
    for r in moneyline_validation_rows
    if (
        r.get("fixture_slug")
        or r.get("fixture")
        or r.get("_source_stem")
        or ""
    ).strip()
})'''

if old2 in text:
    text = text.replace(old2, new2)
else:
    # Replace the previously attempted multiline version if present.
    marker = "moneyline_games = len({"
    idx = text.find(marker)
    if idx != -1:
        end = text.find("})", idx)
        if end != -1:
            end += 2
            text = text[:idx] + new2 + text[end:]
        else:
            print("Could not safely replace moneyline_games block.")
    else:
        print("moneyline_games block not found.")

path.write_text(text, encoding="utf-8")
print("patched", path)

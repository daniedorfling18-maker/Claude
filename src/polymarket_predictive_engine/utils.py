from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def ensure_dir(path: str | Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path



def replace_with_retry(temp_path: Path, path: Path, attempts: int = 30, delay: float = 0.15) -> None:
    last_exc = None
    for i in range(attempts):
        try:
            temp_path.replace(path)
            return
        except FileNotFoundError:
            if not temp_path.exists():
                raise
            raise
        except OSError as exc:
            last_exc = exc
            time.sleep(delay * min(i + 1, 6))
    raise last_exc


def read_csv_rows(path: str | Path, limit: int | None = None) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
    return rows


def csv_columns(path: str | Path) -> list[str]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        try:
            return [c.strip() for c in next(reader)]
        except StopIteration:
            return []


def write_csv(path: str | Path, rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str] | None = None) -> Path:
    rows = list(rows)
    path = Path(path)
    ensure_dir(path.parent)
    if fieldnames is None:
        keys: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(str(key))
        fieldnames = keys
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: serialize_value(row.get(k, "")) for k in fieldnames})
        f.flush()
        os.fsync(f.fileno())
    replace_with_retry(temp_path, path)
    return path


def serialize_value(value: Any) -> str:
    if value is None:
        return ""
    if is_dataclass(value):
        return json.dumps(asdict(value), sort_keys=True)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def write_json(path: str | Path, payload: Any) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    last_exc: Exception | None = None
    for attempt in range(6):
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.{attempt}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True, default=serialize_value)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            replace_with_retry(temp_path, path)
            return path
        except FileNotFoundError as exc:
            last_exc = exc
            try:
                temp_path.unlink()
            except OSError:
                pass
            time.sleep(0.05 * min(attempt + 1, 6))
    if last_exc is not None:
        raise last_exc
    return path


def read_json(path: str | Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_yaml(path: str | Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load polymarket predictive config files") from exc
    with Path(path).open("r", encoding="utf-8-sig") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} must be a mapping")
    return data


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git_commit_hash(repo_root: str | Path | None = None) -> str:
    cwd = str(repo_root or Path.cwd())
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


def discover_files(root: str | Path, patterns: Sequence[str]) -> list[Path]:
    base = Path(root)
    found: list[Path] = []
    for pattern in patterns:
        found.extend(base.glob(pattern))
    return sorted({p for p in found if p.is_file()})


def infer_category(path: str | Path) -> str:
    parts = [p.lower() for p in Path(path).parts]
    for marker in ("polymarket_wide", "polymarket_fixed"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    text = " ".join(parts)
    for cat in ["bitcoin", "crypto", "election", "fed", "finance", "soccer", "sports", "trump"]:
        if cat in text:
            return cat
    return "unknown"


def normalize_slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def find_first_column(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    for column in columns:
        lc = column.lower()
        if any(candidate.lower() in lc for candidate in candidates):
            return column
    return None


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

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


def write_text_atomic(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Replace a text artifact only after its complete sibling temp file is durable."""
    path = Path(path)
    ensure_dir(path.parent)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp_path.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return path


def append_csv_rows(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
) -> Path:
    """Append rows without ever rewriting bytes already present.

    This is the only safe write primitive for a WO-61 ``append_only`` ledger.
    A schema change must use a new versioned path; changing an existing header
    would invalidate every historical prefix anchor.
    """

    path = Path(path)
    rows = list(rows)
    expected = [str(field) for field in fieldnames]
    ensure_dir(path.parent)
    write_header = not path.exists() or path.stat().st_size == 0
    if not write_header:
        actual = csv_columns(path)
        if actual != expected:
            raise ValueError(
                f"append-only CSV schema mismatch for {path}: "
                f"existing={actual!r}, requested={expected!r}; use a new versioned ledger path"
            )
    if not rows:
        return path
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=expected, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: serialize_value(row.get(key, "")) for key in expected})
        f.flush()
        os.fsync(f.fileno())
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
    for candidate in ("bitcoin", "crypto", "election", "fed", "finance", "soccer", "sports", "trump", "worldcup", "all"):
        if candidate in parts:
            return candidate
    return "unknown"


def find_first_column(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    for cand in candidates:
        c_low = cand.lower()
        for col in columns:
            if c_low in col.lower():
                return col
    return None


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def boolish(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "live", "approved"}


def normalize_slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def file_summary(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    stat = path.stat()
    return {"path": str(path), "file_size": stat.st_size, "last_modified_timestamp": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime(ISO_FORMAT)}

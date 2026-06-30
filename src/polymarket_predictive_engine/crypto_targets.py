"""Generate Deribit-compatible crypto fundamental targets from current Polymarket rows.

The Deribit fundamental builder prices terminal digital claims of the form
``P(S_T > K)`` (and, by complement, ``P(S_T < K)``). This module deliberately
does not convert path-dependent markets such as "hit", "touch", or "dip to"
because those need a barrier-option methodology rather than a terminal option
curve. False precision here would create fake edge.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

TARGET_FIELDS = [
    "token_id",
    "currency",
    "strike",
    "expiry",
    "direction",
    "market_slug",
    "question",
    "outcome",
    "source_file",
    "methodology_note",
]

REJECTION_FIELDS = [
    "source_file",
    "market_slug",
    "question",
    "outcome",
    "token_id",
    "reason",
]

_CURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BTC", re.compile(r"\b(?:btc|bitcoin)\b", re.I)),
    ("ETH", re.compile(r"\b(?:eth|ether|ethereum)\b", re.I)),
    ("SOL", re.compile(r"\b(?:sol|solana)\b", re.I)),
    ("XRP", re.compile(r"\b(?:xrp|ripple)\b", re.I)),
)

_ABOVE_RE = re.compile(r"\b(?:above|over|exceed(?:s|ed)?|greater than|higher than|at or above)\b", re.I)
_BELOW_RE = re.compile(r"\b(?:below|under|less than|lower than|at or below)\b", re.I)
_PATH_DEPENDENT_RE = re.compile(
    r"\b(?:hit|hits|reach|reaches|touch|touches|dip|dips|drop|drops|fall|falls|"
    r"go(?:es)? to|trade(?:s)? at|anytime|any time|intraday|all[- ]time high|ath)\b",
    re.I,
)
_MONEY_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.\d+)?)\s*([kKmM])?")
_DIRECTIONAL_NUMBER_RE = re.compile(
    r"\b(?:above|over|below|under|greater than|less than|higher than|lower than)\s+"
    r"([0-9]+(?:\.\d+)?)\s*([kKmM])\b",
    re.I,
)


def _text(row: Mapping[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("question", "market_slug", "event_slug", "event_title", "category", "outcome")
    )


def infer_currency(text: str) -> str | None:
    for currency, pattern in _CURRENCY_PATTERNS:
        if pattern.search(text):
            return currency
    return None


def infer_strike(text: str) -> float | None:
    match = _MONEY_RE.search(text)
    if match is None:
        match = _DIRECTIONAL_NUMBER_RE.search(text)
    if match is None:
        return None
    number = safe_float(match.group(1).replace(",", ""))
    if number is None:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return float(number)


def infer_question_direction(text: str) -> str | None:
    above = bool(_ABOVE_RE.search(text))
    below = bool(_BELOW_RE.search(text))
    if above == below:
        return None
    return "above" if above else "below"


def is_path_dependent(text: str) -> bool:
    return bool(_PATH_DEPENDENT_RE.search(text))


def direction_for_outcome(question_direction: str, outcome: str) -> str | None:
    outcome_norm = str(outcome or "").strip().lower()
    positive = outcome_norm in {"yes", "y", "true", "above", "over", "up"}
    negative = outcome_norm in {"no", "n", "false", "below", "under", "down"}
    if not positive and not negative:
        return None
    if positive:
        return question_direction
    return "below" if question_direction == "above" else "above"


def _expiry(row: Mapping[str, Any]) -> str | None:
    for key in ("expiry", "close_time", "end_date", "endDate", "end_time"):
        value = row.get(key)
        if not str(value or "").strip():
            continue
        parsed = parse_timestamp(value)
        if parsed:
            return parsed.date().isoformat()
        # ``build_crypto_fundamental`` also accepts YYYY-MM-DD.
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
    return None


def target_from_row(row: Mapping[str, Any], *, source_file: str = "") -> tuple[dict[str, Any] | None, str]:
    token_id = str(row.get("token_id") or row.get("asset_id") or "").strip()
    if not token_id:
        return None, "missing_token_id"
    text = _text(row)
    currency = infer_currency(text)
    if not currency:
        return None, "no_supported_currency"
    strike = infer_strike(text)
    if strike is None:
        return None, "no_strike"
    if is_path_dependent(text):
        return None, "path_dependent_payoff_needs_barrier_model"
    question_direction = infer_question_direction(text)
    if question_direction is None:
        return None, "no_terminal_above_below_direction"
    direction = direction_for_outcome(question_direction, str(row.get("outcome") or ""))
    if direction is None:
        return None, "unsupported_outcome"
    expiry = _expiry(row)
    if expiry is None:
        return None, "missing_expiry"
    return (
        {
            "token_id": token_id,
            "currency": currency,
            "strike": strike,
            "expiry": expiry,
            "direction": direction,
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "outcome": row.get("outcome", ""),
            "source_file": source_file,
            "methodology_note": "terminal_option_curve_only; path_dependent_markets_rejected",
        },
        "",
    )


def _configured_input_paths(cfg: EngineConfig, settings: Mapping[str, Any]) -> list[Path]:
    configured = settings.get("input_paths")
    if configured:
        return [Path(str(path)) for path in configured]
    return [
        cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        cfg.governance_root / "websocket_liquidity_targets.csv",
    ]


def build_crypto_targets(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("crypto_target_generation", {}) or {}
    output_path = Path(
        settings.get("output_path")
        or (cfg.raw.get("crypto_fundamental", {}) or {}).get("targets_path")
        or "inputs/polymarket/crypto_targets.csv"
    )
    max_targets = int(settings.get("max_targets", 250) or 250)
    targets: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    source_rows = 0

    for input_path in _configured_input_paths(cfg, settings):
        rows = read_csv_rows(input_path)
        source_rows += len(rows)
        for row in rows:
            target, reason = target_from_row(row, source_file=str(input_path))
            if target is None:
                # Store only rows that looked crypto-related enough to be decision-useful.
                text = _text(row)
                if infer_currency(text) or str(row.get("category") or "").lower().startswith("crypto"):
                    rejections.append(
                        {
                            "source_file": str(input_path),
                            "market_slug": row.get("market_slug", ""),
                            "question": row.get("question", ""),
                            "outcome": row.get("outcome", ""),
                            "token_id": row.get("token_id") or row.get("asset_id") or "",
                            "reason": reason,
                        }
                    )
                continue
            token_id = str(target.get("token_id") or "")
            if token_id in seen_tokens:
                continue
            seen_tokens.add(token_id)
            targets.append(target)
            if len(targets) >= max_targets:
                break
        if len(targets) >= max_targets:
            break

    write_csv(output_path, targets, fieldnames=TARGET_FIELDS)
    rejection_path = cfg.governance_root / "crypto_target_rejections.csv"
    write_csv(rejection_path, rejections, fieldnames=REJECTION_FIELDS)
    reason_counts = dict(Counter(row["reason"] for row in rejections).most_common())
    summary = {
        "status": "built" if targets else "no_terminal_targets",
        "source_rows": source_rows,
        "target_rows": len(targets),
        "rejected_rows": len(rejections),
        "top_rejection_reasons": reason_counts,
        "output_file": str(output_path),
        "rejection_file": str(rejection_path),
        "generated_at_utc": now_utc(),
        "note": (
            "Generates Deribit terminal above/below targets only. Hit/touch/dip/reach markets "
            "are rejected until a barrier-option method is added."
        ),
    }
    write_json(cfg.governance_root / "crypto_targets_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_crypto_targets(load_config(config_path))

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import EngineConfig, load_config
from .resolution_collector import infer_market_resolution_rows
from .resolution_corpus import append_resolution_observations, canonical_utc
from .utils import normalize_external_timestamp, write_csv, write_json

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com/markets"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _market_close_dt(market: dict[str, Any]) -> datetime:
    for key in ("closedTime", "closed_time", "endDate", "endDateIso", "updatedAt", "createdAt"):
        seconds = normalize_external_timestamp(market.get(key))
        if seconds is not None:
            return datetime.fromtimestamp(seconds, timezone.utc)
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_closed_candidate(market: dict[str, Any]) -> bool:
    return _truthy(market.get("closed"))


# Corpus stratification.
#
# The resolved corpus used to be "the N most recently closed markets"
# (closed=true, order=closedTime, ascending=false). Sports fixtures and
# sub-hourly crypto candles resolve continuously while a macro or political
# question resolves once, so that rule cannot produce anything but sport and
# candles no matter how long it runs. Measured 2026-08-15: of 1000 corpus rows,
# the top resolution sources were ligue2 (131), MLB (118), flashscore (78),
# ATP (69), PGA (69) and setkacup (66) - and the corpus shared ZERO members with
# the 40 markets the study was actually quoting. Every directional statistic
# computed on it (Brier, CLV, the rule search) was therefore measuring a
# different population from the one being traded.
#
# Two corrections below: drop the populations that cannot carry a directional
# signal, then take a per-family quota instead of a global recency slice.

DEFAULT_MIN_MARKET_DURATION_HOURS = 6.0
# "btc-up-or-down-5m", "xrp-up-or-down-15m", "eth-up-or-down-1h" - the venue
# names its own candle series with the window length, which is a far more
# reliable signal than any timestamp on the market.
_CANDLE_SERIES_PATTERN = re.compile(r"up-or-down-\d+\s*(m|min|h|hr)\b|up-or-down-\d+(m|h)$")
# Domains that publish per-fixture results. A market resolved by one of these is
# a single sporting fixture, priced against the sharpest books in the world.
DEFAULT_EXCLUDED_RESOLUTION_DOMAINS = (
    "setkacup.com",
    "ligue2.fr",
    "mlb.com",
    "flashscore.com",
    "atptour.com",
    "pgatour.com",
    "slstat.com",
    "uefa.com",
    "wtatennis.com",
    "ekstraklasa.org",
)


def _market_open_dt(market: dict[str, Any]) -> datetime | None:
    for key in ("startDate", "startDateIso", "start_date", "createdAt"):
        seconds = normalize_external_timestamp(market.get(key))
        if seconds is not None:
            return datetime.fromtimestamp(seconds, timezone.utc)
    return None


def _market_duration_hours(market: dict[str, Any]) -> float | None:
    opened = _market_open_dt(market)
    if opened is None:
        return None
    closed = _market_close_dt(market)
    if closed <= datetime(1971, 1, 1, tzinfo=timezone.utc):
        return None
    delta = (closed - opened).total_seconds() / 3600.0
    return delta if delta >= 0 else None


def _resolution_domain(market: dict[str, Any]) -> str:
    source = str(market.get("resolutionSource") or "").strip().lower()
    if not source:
        return ""
    source = source.split("://", 1)[-1]
    return source.split("/", 1)[0].removeprefix("www.")


def _series_slug(market: dict[str, Any]) -> str:
    """The venue's own recurring-series identifier, if the market has one."""

    events = market.get("events")
    if not isinstance(events, list):
        return ""
    for event in events:
        if not isinstance(event, dict):
            continue
        series = event.get("series")
        if not isinstance(series, list):
            continue
        for entry in series:
            if isinstance(entry, dict):
                slug = str(entry.get("slug") or "").strip().lower()
                if slug:
                    return slug
    return ""


def _market_family(market: dict[str, Any]) -> str:
    """Stratification key: the population a market belongs to.

    Category first (Gamma's own label), then the resolution domain, then an
    explicit ``uncategorised`` bucket. Never the question text, which is too
    granular to stratify on.
    """

    # Series first, measured against the live Gamma feed on 2026-08-15: of the
    # 100 most recently closed markets, 100 carried an events[].series[].slug
    # and ZERO carried a `category`, so the category branch below never fired.
    # Series is also the right granularity - "itf", "dota-2", "setkameua-games",
    # "btc-up-or-down-5m" - which is exactly what the per-family cap needs to
    # bound. Every continuously-resolving population carries one; the one-off
    # questions this study actually trades mostly do not.
    series = _series_slug(market)
    if series:
        return f"series:{series}"
    category = str(market.get("category") or "").strip().lower()
    if category:
        return category
    domain = _resolution_domain(market)
    if domain:
        return f"source:{domain}"
    # Slug prefix before falling back to one shared bucket. Over-splitting is
    # safe here - a family below the cap simply contributes everything it has -
    # whereas a single giant "uncategorised" family would hit the cap and
    # starve the quota for markets Gamma happens not to label.
    slug = str(market.get("slug") or "").strip().lower()
    if slug:
        tokens = [token for token in slug.split("-") if token and not token.isdigit()]
        if tokens:
            return "slug:" + "-".join(tokens[:2])
    return "uncategorised"


def _excluded_population(
    market: dict[str, Any],
    *,
    min_duration_hours: float,
    excluded_domains: tuple[str, ...],
) -> str:
    """Return the exclusion reason, or "" when the market is eligible."""

    domain = _resolution_domain(market)
    if domain and any(domain == bad or domain.endswith(f".{bad}") for bad in excluded_domains):
        return f"per_fixture_sport:{domain}"
    # Sub-hourly "Up or Down" candles: a coin flip by construction, and the
    # population that drove the corpus base rate to exactly 0.5000.
    #
    # Matched on the venue's own series name, NOT on duration. Measured against
    # the live feed 2026-08-15: "XRP Up or Down - August 15, 6:00AM-6:15AM ET"
    # is a fifteen-MINUTE market whose startDate is the LISTING time a day
    # earlier, so close-minus-start reads 24.1 hours and sails over any sane
    # duration floor. startDate is when the market was listed, not when its
    # event window opens, so duration cannot identify this population at all.
    series = _series_slug(market)
    if series and _CANDLE_SERIES_PATTERN.search(series):
        return "sub_hourly_candle_series"
    duration = _market_duration_hours(market)
    if duration is not None and duration < min_duration_hours:
        return "sub_duration_floor"
    return ""


def _stratified_take(
    candidates: list[dict[str, Any]],
    *,
    requested: int,
    max_share_per_family: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Take a per-family quota rather than a global recency slice.

    Families are drawn round-robin, newest first within each, so no single
    continuously-resolving population can crowd out the rest. A family is capped
    at ``max_share_per_family`` of the request.
    """

    by_family: dict[str, list[dict[str, Any]]] = {}
    for market in candidates:
        by_family.setdefault(_market_family(market), []).append(market)
    for rows in by_family.values():
        rows.sort(key=_market_close_dt, reverse=True)

    cap = max(1, int(requested * max_share_per_family)) if max_share_per_family > 0 else requested
    selected: list[dict[str, Any]] = []
    taken: dict[str, int] = {family: 0 for family in by_family}
    families = sorted(by_family)
    progressed = True
    while len(selected) < requested and progressed:
        progressed = False
        for family in families:
            if len(selected) >= requested:
                break
            rows = by_family[family]
            index = taken[family]
            if index >= len(rows) or index >= cap:
                continue
            selected.append(rows[index])
            taken[family] = index + 1
            progressed = True

    selected.sort(key=_market_close_dt, reverse=True)
    composition = {family: count for family, count in sorted(taken.items(), key=lambda kv: -kv[1]) if count}
    return selected, {
        "families_available": len(by_family),
        "families_selected": len(composition),
        "per_family_cap": cap,
        "composition": composition,
        "requested_markets": requested,
        "selected_markets": len(selected),
        # A stratified quota can under-fill where a recency slice never would,
        # because most of the feed is excluded. Surfaced rather than inferred:
        # a short corpus is a finding about the venue, not a silent shortfall.
        "quota_met": len(selected) >= requested,
    }


def _as_market_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "markets", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _fetch_page(base_url: str, params: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], str]:
    try:
        response = requests.get(base_url, params=params, timeout=timeout)
        if response.status_code == 422:
            return [], "pagination_limit"
        response.raise_for_status()
        return _as_market_list(response.json()), "ok"
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) == 422:
            return [], "pagination_limit"
        raise


def _append_unique(candidates: list[dict[str, Any]], seen: set[str], market: dict[str, Any]) -> None:
    market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
    if not market_id or market_id in seen:
        return
    seen.add(market_id)
    if _is_closed_candidate(market):
        candidates.append(market)


def _scan_feed(
    *,
    base_url: str,
    timeout: int,
    page_size: int,
    max_pages: int,
    base_params: dict[str, Any],
    requested_closed_markets: int,
    label: str,
    progress_every_pages: int,
    candidate_buffer: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages_scanned = 0
    rows_seen = 0
    stop_reason = "max_pages"

    for page in range(max_pages):
        params = dict(base_params)
        params["limit"] = page_size
        params["offset"] = page * page_size
        rows, status = _fetch_page(base_url, params, timeout)
        pages_scanned += 1

        if status == "pagination_limit":
            stop_reason = "pagination_limit"
            break
        if not rows:
            stop_reason = "empty_page"
            break

        rows_seen += len(rows)
        for market in rows:
            _append_unique(candidates, seen, market)

        if progress_every_pages and (pages_scanned % progress_every_pages == 0):
            newest = _market_close_dt(sorted(candidates, key=_market_close_dt, reverse=True)[0]).isoformat() if candidates else ""
            print(
                f"backfill {label} progress: pages={pages_scanned}; rows_seen={rows_seen}; closed_candidates={len(candidates)}; newest_candidate={newest}",
                flush=True,
            )

        # Closed=true can return very old markets first, so do not stop too early
        # unless a useful number of candidates has already been collected. Under
        # stratification the caller raises this: most of what the feed returns is
        # excluded, so a buffer sized for the unfiltered case starves the quota.
        target_buffer = candidate_buffer or max(requested_closed_markets * 4, requested_closed_markets + 100)
        if len(candidates) >= target_buffer:
            stop_reason = "candidate_buffer_reached"
            break

    candidates.sort(key=_market_close_dt, reverse=True)
    return candidates[:requested_closed_markets], {
        "label": label,
        "pages_scanned": pages_scanned,
        "rows_seen": rows_seen,
        "closed_candidates": len(candidates),
        "stop_reason": stop_reason,
    }


def _candidate_markets(
    *,
    base_url: str,
    page_size: int,
    max_pages: int,
    timeout: int,
    gamma_query_params: dict[str, Any],
    requested_closed_markets: int,
    progress_every_pages: int,
    stratify: bool,
    min_duration_hours: float,
    excluded_domains: tuple[str, ...],
    max_share_per_family: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    scans: list[dict[str, Any]] = []
    # Measured on the real feed: the excluded populations are the overwhelming
    # majority of recently-closed markets, so the scan must page far deeper to
    # fill a stratified quota than it did to fill a recency slice.
    scan_buffer = (
        max(requested_closed_markets * 25, requested_closed_markets + 2000)
        if stratify
        else max(requested_closed_markets * 4, requested_closed_markets + 100)
    )

    # 1) Explicit closed pagination. Gamma's closed=true feed starts with ancient
    # rows, so we page forward until either we collect a large buffer or hit the
    # API pagination cap, then sort locally by close time.
    closed_params = dict(gamma_query_params or {})
    closed_params["closed"] = "true"
    closed_params.setdefault("order", "closedTime")
    closed_params.setdefault("ascending", "false")
    closed_candidates, closed_meta = _scan_feed(
        base_url=base_url,
        timeout=timeout,
        page_size=page_size,
        max_pages=max_pages,
        base_params=closed_params,
        requested_closed_markets=requested_closed_markets,
        candidate_buffer=scan_buffer,
        label="closed_true",
        progress_every_pages=progress_every_pages,
    )
    scans.append(closed_meta)
    for market in closed_candidates:
        _append_unique(all_candidates, seen, market)

    # 2) Default recent feed as a supplement. This often contains active markets,
    # but keeping it as a supplement is useful when Gamma returns recently closed
    # markets in the default feed.
    recent_params = dict(gamma_query_params or {})
    recent_params.pop("closed", None)
    recent_candidates, recent_meta = _scan_feed(
        base_url=base_url,
        timeout=timeout,
        page_size=page_size,
        max_pages=max_pages,
        base_params=recent_params,
        requested_closed_markets=requested_closed_markets,
        candidate_buffer=scan_buffer,
        label="default_recent",
        progress_every_pages=progress_every_pages,
    )
    scans.append(recent_meta)
    for market in recent_candidates:
        _append_unique(all_candidates, seen, market)

    all_candidates.sort(key=_market_close_dt, reverse=True)
    merged_total = len(all_candidates)

    if not stratify:
        return all_candidates[:requested_closed_markets], {
            "strategy": "closed_true_paginated_plus_default_recent_supplement",
            "scans": scans,
            "merged_closed_candidates": merged_total,
            "stratified": False,
        }

    eligible: list[dict[str, Any]] = []
    excluded_by_reason: dict[str, int] = {}
    for market in all_candidates:
        reason = _excluded_population(
            market,
            min_duration_hours=min_duration_hours,
            excluded_domains=excluded_domains,
        )
        if reason:
            key = reason.split(":", 1)[0]
            excluded_by_reason[key] = excluded_by_reason.get(key, 0) + 1
            continue
        eligible.append(market)

    selected, strata_meta = _stratified_take(
        eligible,
        requested=requested_closed_markets,
        max_share_per_family=max_share_per_family,
    )
    return selected, {
        "strategy": "closed_true_plus_recent_then_population_stratified",
        "scans": scans,
        "merged_closed_candidates": merged_total,
        "stratified": True,
        "eligible_after_exclusions": len(eligible),
        "excluded_by_reason": excluded_by_reason,
        "min_duration_hours": min_duration_hours,
        # Reported so corpus composition is visible BEFORE any statistic is
        # computed on it - the failure this stratification exists to prevent.
        **strata_meta,
    }


def historical_backfill(
    cfg: EngineConfig,
    historical_limit: int | None = None,
    *,
    allow_old_history: bool = False,
    as_of: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    # A production observation timestamp must not predate the HTTP response
    # whose terminal state it records.  Explicit ``as_of`` is retained only as
    # a deterministic replay/test clock; the CLI never supplies it.
    replay_run_at = canonical_utc(as_of) if as_of is not None else None
    settings = cfg.raw.get("historical_backfill", {})
    base_url = str(settings.get("gamma_base_url", DEFAULT_GAMMA_BASE_URL))
    requested = int(historical_limit or settings.get("max_closed_markets", 250))
    page_size = int(settings.get("page_size", 100))
    max_pages = int(settings.get("max_pages", 25))
    timeout = int(settings.get("request_timeout_seconds", 30))
    progress_every_pages = int(settings.get("progress_every_pages", 5))
    gamma_query_params = dict(settings.get("gamma_query_params", {}) or {})
    max_age_days = int(settings.get("max_age_days", 365))

    # Corpus stratification. Defaults are ON: the unstratified corpus was
    # measured to share zero members with the traded universe, so recency-only
    # sampling is the defect, not the baseline.
    strata_settings = dict(settings.get("corpus_stratification", {}) or {})
    stratify = bool(strata_settings.get("enabled", True))
    min_duration_hours = float(strata_settings.get("min_market_duration_hours", DEFAULT_MIN_MARKET_DURATION_HOURS))
    excluded_domains = tuple(
        str(domain).strip().lower().removeprefix("www.")
        for domain in (strata_settings.get("excluded_resolution_domains") or DEFAULT_EXCLUDED_RESOLUTION_DOMAINS)
        if str(domain).strip()
    )
    max_share_per_family = float(strata_settings.get("max_share_per_family", 0.15))

    candidates, fetch_meta = _candidate_markets(
        base_url=base_url,
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        gamma_query_params=gamma_query_params,
        requested_closed_markets=requested,
        progress_every_pages=progress_every_pages,
        stratify=stratify,
        min_duration_hours=min_duration_hours,
        excluded_domains=excluded_domains,
        max_share_per_family=max_share_per_family,
    )
    run_at = replay_run_at or canonical_utc(None)
    run_clock = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
    cutoff = run_clock - timedelta(days=max_age_days)
    old_candidates = [market for market in candidates if _market_close_dt(market) < cutoff]
    if old_candidates and not allow_old_history:
        candidates = [market for market in candidates if _market_close_dt(market) >= cutoff]

    resolution_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    errors = 0

    for market in candidates:
        try:
            rows, quality = infer_market_resolution_rows(
                market,
                observed_at_utc=run_at,
            )
            resolution_rows.extend(rows)
            quality_rows.extend(quality)
        except Exception as exc:
            errors += 1
            quality_rows.append(
                {
                    "gamma_market_id": market.get("id", ""),
                    "market_slug": market.get("slug", ""),
                    "condition_id": market.get("conditionId", ""),
                    "resolution_quality": "backfill_error",
                    "reason": str(exc),
                }
            )

    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "historical_resolutions.csv", resolution_rows)
    write_csv(gov_root / "historical_resolution_quality_report.csv", quality_rows)
    corpus = append_resolution_observations(
        cfg,
        resolution_rows,
        producer="historical_backfill",
        observed_at_utc=run_at,
    )

    clean_markets = len(
        {
            row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            for row in resolution_rows
            if row.get("resolution_quality") == "clean_settlement"
        }
    )
    close_dates = [_market_close_dt(market).isoformat() for market in candidates[:10]]

    summary = {
        "work_order": "WO-101",
        "requested_closed_markets": requested,
        "fetched_markets": len(candidates),
        "resolution_rows": len(resolution_rows),
        "quality_rows": len(quality_rows),
        "clean_settlement_markets": clean_markets,
        "error_count": errors,
        "fetch_strategy": fetch_meta,
        "historical_cutoff_utc": cutoff.isoformat(),
        "old_candidates_excluded": 0 if allow_old_history else len(old_candidates),
        "old_history_explicitly_allowed": allow_old_history,
        "newest_candidate_close_times": close_dates,
        "collected_at_utc": run_at,
        "output_file": str(out_root / "historical_resolutions.csv"),
        "quality_file": str(gov_root / "historical_resolution_quality_report.csv"),
        "append_only_resolution_corpus": corpus,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(gov_root / "historical_resolution_summary.json", summary)
    return resolution_rows, quality_rows, summary


def main(
    config_path: str,
    historical_limit: int | None = None,
    *,
    allow_old_history: bool = False,
) -> dict[str, Any]:
    _, _, summary = historical_backfill(
        load_config(config_path),
        historical_limit=historical_limit,
        allow_old_history=allow_old_history,
    )
    return summary

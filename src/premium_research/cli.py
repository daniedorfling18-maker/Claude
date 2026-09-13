"""Command line for the WO-166 proof of concept.

This is the only module that runs the fetch loop, reads the clock, or calls
git; ``manifest.py`` and ``runner.py`` read and write only beneath the research
root they are handed. The HTTP calls live in ``binance_vision.fetch_bytes`` and
``deribit_history.fetch_json``; every caller takes them as injectable
callables, so tests never reach the network. Sub-commands:

* ``fetch``            — pull the registered series into ``<root>/data`` and write ``<root>/manifest.json``
* ``verify-manifest``  — recompute every sha256 in the manifest and list unlisted files
* ``run``              — compute Lane A and Lane B from the committed inputs and write ``<root>/results``
* ``verify-results``   — recompute and byte-compare against the committed results

Every failure path exits non-zero and leaves no partial output in place: data
and results are written to a temporary sibling directory and renamed into
place only on success.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import binance_vision, deribit_history
from .manifest import ManifestEntry, build_manifest, sha256_bytes, verify_manifest, write_manifest
from .runner import BINANCE_END_MONTH, BINANCE_START_MONTH, CONFIGS, DERIBIT_FUNDING_START_MS, DERIBIT_INSTRUMENTS, LANE_B_START_MS, SPAN_END_MS, SYMBOLS

REPO_ROOT = Path(__file__).resolve().parents[2]  # src/premium_research/cli.py -> repository root (holds pyproject.toml)
DEFAULT_ROOT = REPO_ROOT / "research" / "premium_poc"
GZIP_THRESHOLD_BYTES = 5_000_000
# Per-request timeouts (60 s per socket operation, 3 attempts) live with the HTTP calls in binance_vision.fetch_bytes and deribit_history.fetch_json.
FETCH_DEADLINE_SECONDS = 14_400.0  # wall clock for the whole fetch; expiry aborts. Basis: WO-166 Timeouts bullet (amended 2026-09-12)

# Registered spans and universe (WO-166) have one implementation site: runner.py. Changing any after results exist is a new work order.
BINANCE_SYMBOLS = SYMBOLS
DVOL_START_MS = LANE_B_START_MS


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_revision(cwd: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"


def uncommitted_paths(cwd: Path, subtree: str) -> list[str] | None:
    """Paths under ``subtree`` with uncommitted changes (staged, unstaged, or untracked); ``None`` when git cannot answer."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", subtree], cwd=str(cwd), capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [line[3:] for line in out.stdout.splitlines() if line.strip()]


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue().encode("utf-8")


def _store(frame: pd.DataFrame, target: Path) -> tuple[Path, bytes]:
    """Write ``frame`` as CSV, gzipped (deterministically) when over the threshold. Returns the final path and bytes."""
    raw = _csv_bytes(frame)
    if len(raw) > GZIP_THRESHOLD_BYTES:
        target = target.with_suffix(target.suffix + ".gz")
        buffer = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as handle:
            handle.write(raw)
        payload = buffer.getvalue()
    else:
        payload = raw
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target, payload


def run_fetch(
    root: Path,
    *,
    fetch_bytes: Callable[[str], bytes] = binance_vision.fetch_bytes,
    fetch_json: deribit_history.FetchJsonFn | None = None,
    now_iso: Callable[[], str] = utc_now_iso,
    code_revision: str | None = None,
    start_month: str = BINANCE_START_MONTH,
    end_month: str = BINANCE_END_MONTH,
    symbols: tuple[str, ...] = BINANCE_SYMBOLS,
    deribit_instruments: dict[str, str] | None = None,
    deribit_funding_start_ms: int = DERIBIT_FUNDING_START_MS,
    dvol_start_ms: int = DVOL_START_MS,
    span_end_ms: int = SPAN_END_MS,
    deadline_seconds: float = FETCH_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Fetch every registered series into ``root/data`` atomically and write ``root/manifest.json``.

    Every request goes through ``fetch_bytes`` / ``fetch_json`` with a per-socket
    timeout; the whole fetch additionally carries a wall-clock deadline checked
    before every request, so a trickling server cannot run unbounded.
    """
    root = Path(root)
    started = clock()

    def progress(message: str) -> None:
        print(f"[fetch +{clock() - started:.0f}s] {message}", file=sys.stderr, flush=True)

    def guarded_bytes(url: str) -> bytes:
        if clock() - started > deadline_seconds:
            raise RuntimeError(f"fetch exceeded its wall-clock deadline of {deadline_seconds:.0f} s")
        return fetch_bytes(url)

    def guarded_json(url: str, params):
        if clock() - started > deadline_seconds:
            raise RuntimeError(f"fetch exceeded its wall-clock deadline of {deadline_seconds:.0f} s")
        return (fetch_json or deribit_history.fetch_json)(url, params)

    data_dir = root / "data"
    if data_dir.exists() or (root / "manifest.json").exists():
        raise RuntimeError(f"{root} already holds committed inputs; the fetch never overwrites them")
    root.mkdir(parents=True, exist_ok=True)
    tmp_root = root / f".fetch-tmp-{os.getpid()}"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_data = tmp_root / "data"
    tmp_data.mkdir(parents=True)
    instruments = deribit_instruments or DERIBIT_INSTRUMENTS
    months = binance_vision.month_range(start_month, end_month)
    entries: list[ManifestEntry] = []
    try:
        for symbol in symbols:
            fetched_at = now_iso()
            progress(f"{symbol} funding: {len(months)} months")
            funding, digests = binance_vision.download_funding(symbol, months, fetch=guarded_bytes)
            funding.insert(1, "calc_time_iso", funding["calc_time"].map(binance_vision.iso_utc))
            funding = funding[["calc_time", "calc_time_iso", "calc_time_raw", "funding_interval_hours", "last_funding_rate"]]
            path, payload = _store(funding, tmp_data / "binance" / f"{symbol}_funding_8h.csv")
            entries.append(
                ManifestEntry(
                    path=str(path.relative_to(tmp_root)).replace("\\", "/"),
                    source_urls=sorted(digests),
                    fetched_at=fetched_at,
                    rows=int(len(funding)),
                    first_timestamp_ms=int(funding["calc_time"].iloc[0]),
                    last_timestamp_ms=int(funding["calc_time"].iloc[-1]),
                    sha256=sha256_bytes(payload),
                    upstream_checksums=dict(digests),
                    checksum_verified=True,
                    notes={"grid": "8h, validated gap-free", "series": "binance_um_funding"},
                )
            )
            for market, label in (("um", "perp"), ("spot", "spot")):
                fetched_at = now_iso()
                progress(f"{symbol} {label} 1h klines: {len(months)} months")
                klines, kdigests, gaps = binance_vision.download_klines(market, symbol, "1h", months, fetch=guarded_bytes)
                klines.insert(1, "open_time_iso", klines["open_time"].map(binance_vision.iso_utc))
                path, payload = _store(klines, tmp_data / "binance" / f"{symbol}_{label}_1h.csv")
                entries.append(
                    ManifestEntry(
                        path=str(path.relative_to(tmp_root)).replace("\\", "/"),
                        source_urls=sorted(kdigests),
                        fetched_at=fetched_at,
                        rows=int(len(klines)),
                        first_timestamp_ms=int(klines["open_time"].iloc[0]),
                        last_timestamp_ms=int(klines["open_time"].iloc[-1]),
                        sha256=sha256_bytes(payload),
                        upstream_checksums=dict(kdigests),
                        checksum_verified=True,
                        notes={"series": f"binance_{market}_klines_1h", "columns": "open_time,open,high,low,close", **gaps},
                    )
                )
        for currency, instrument in instruments.items():
            fetched_at = now_iso()
            progress(f"{instrument} funding history")
            funding, calls = deribit_history.fetch_funding_history(instrument, deribit_funding_start_ms, span_end_ms, fetch=guarded_json)
            funding.insert(1, "timestamp_iso", funding["timestamp"].map(binance_vision.iso_utc))
            path, payload = _store(funding, tmp_data / "deribit" / f"{currency}_funding_1h.csv")
            entries.append(
                ManifestEntry(
                    path=str(path.relative_to(tmp_root)).replace("\\", "/"),
                    source_urls=calls,
                    fetched_at=fetched_at,
                    rows=int(len(funding)),
                    first_timestamp_ms=int(funding["timestamp"].iloc[0]),
                    last_timestamp_ms=int(funding["timestamp"].iloc[-1]),
                    sha256=sha256_bytes(payload),
                    notes={"series": "deribit_funding_history_1h", "instrument": instrument, "pages": len(calls)},
                )
            )
            fetched_at = now_iso()
            progress(f"{currency} DVOL daily")
            dvol, dcalls = deribit_history.fetch_dvol(currency, dvol_start_ms, span_end_ms, fetch=guarded_json)
            dvol.insert(1, "timestamp_iso", dvol["timestamp"].map(binance_vision.iso_utc))
            path, payload = _store(dvol, tmp_data / "deribit" / f"{currency}_dvol_daily.csv")
            entries.append(
                ManifestEntry(
                    path=str(path.relative_to(tmp_root)).replace("\\", "/"),
                    source_urls=dcalls,
                    fetched_at=fetched_at,
                    rows=int(len(dvol)),
                    first_timestamp_ms=int(dvol["timestamp"].iloc[0]),
                    last_timestamp_ms=int(dvol["timestamp"].iloc[-1]),
                    sha256=sha256_bytes(payload),
                    notes={"series": "deribit_dvol_daily", "pages": len(dcalls)},
                )
            )
        manifest = build_manifest(
            entries,
            generated_at=now_iso(),
            span={
                "binance_months": f"{start_month}..{end_month}",
                "deribit_funding": f"{binance_vision.iso_utc(deribit_funding_start_ms)}..{binance_vision.iso_utc(span_end_ms)}",
                "dvol": f"{binance_vision.iso_utc(dvol_start_ms)}..{binance_vision.iso_utc(span_end_ms)}",
            },
            code_revision=code_revision if code_revision is not None else git_revision(REPO_ROOT),
        )
        write_manifest(manifest, tmp_root / "manifest.json")
    except BaseException:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    os.replace(tmp_data, data_dir)
    os.replace(tmp_root / "manifest.json", root / "manifest.json")
    shutil.rmtree(tmp_root, ignore_errors=True)
    return manifest


def _cmd_fetch(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        manifest = run_fetch(root)
    except (binance_vision.VisionError, deribit_history.DeribitError, RuntimeError) as exc:
        print(f"FETCH ABORTED: {exc}", file=sys.stderr)
        return 2
    print(f"fetched {len(manifest['entries'])} series into {root / 'data'}; manifest at {root / 'manifest.json'}")
    return 0


def _cmd_verify_manifest(args: argparse.Namespace) -> int:
    root = Path(args.root)
    failures = verify_manifest(root / "manifest.json", root)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("manifest OK")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .runner import CONFIGS, run_all

    # The results record code_revision = HEAD and verify-results recomputes with the stored string, so a
    # pass from a tree whose estimator code differs from HEAD would record a revision that cannot reproduce
    # it (WO-167 delta 2). Fail closed: no result from a dirty estimator tree.
    dirty = uncommitted_paths(REPO_ROOT, "src/premium_research")
    if dirty is None or dirty:
        detail = "git status unavailable" if dirty is None else ", ".join(dirty[:5])
        print(f"RUN ABORTED: src/premium_research has uncommitted changes ({detail}); the recorded code_revision would not reproduce the results", file=sys.stderr)
        return 2
    try:
        summary = run_all(Path(args.root), code_revision=git_revision(REPO_ROOT), generated_at=utc_now_iso(), force=args.force, config=CONFIGS[args.work_order])
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"RUN ABORTED: {exc}", file=sys.stderr)
        return 2
    print(summary)
    return 0


def _cmd_verify_results(args: argparse.Namespace) -> int:
    from .runner import CONFIGS, verify_results

    failures = verify_results(Path(args.root), config=CONFIGS[args.work_order])
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("results OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="premium_research", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="research tree root (default: research/premium_poc under the repository root, anchored off this file)")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download the registered series and write the manifest")
    fetch.set_defaults(func=_cmd_fetch)
    verify = sub.add_parser("verify-manifest", help="recompute every sha256 in the manifest")
    verify.set_defaults(func=_cmd_verify_manifest)
    run = sub.add_parser("run", help="compute Lane A and Lane B from the committed inputs")
    run.add_argument("--force", action="store_true", help="replace an existing results directory")
    run.add_argument("--work-order", choices=tuple(CONFIGS), default="WO-166", help="registered configuration to run (WO-167: perpetual-side completeness scope, results_wo167/)")
    run.set_defaults(func=_cmd_run)
    verify_results_cmd = sub.add_parser("verify-results", help="recompute and byte-compare the committed results")
    verify_results_cmd.add_argument("--work-order", choices=tuple(CONFIGS), default="WO-166", help="which committed results to verify, under that work order's registered configuration")
    verify_results_cmd.set_defaults(func=_cmd_verify_results)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

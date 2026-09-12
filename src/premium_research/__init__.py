"""Offline, pre-registered proof-of-concept research on public historical data (WO-166).

This package imports nothing from ``polymarket_predictive_engine``, reads nothing
under ``paths.output_root``, and writes only under ``research/premium_poc/``.
Only :mod:`premium_research.cli` runs the fetch loop, reads the clock, or calls
git; ``manifest`` and ``runner`` read and write only beneath the research root
they are handed; the HTTP calls live in ``binance_vision.fetch_bytes`` and
``deribit_history.fetch_json`` behind injectable callables; ``carry``, ``vrp``,
``bootstrap`` and ``report`` are pure functions over in-memory data.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

"""Offline, pre-registered proof-of-concept research on public historical data (WO-166).

This package imports nothing from ``polymarket_predictive_engine``, reads nothing
under ``paths.output_root``, and writes only under ``research/premium_poc/``.
Only :mod:`premium_research.cli` touches the network, the clock, or the
filesystem; every other module is a pure function over in-memory data.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

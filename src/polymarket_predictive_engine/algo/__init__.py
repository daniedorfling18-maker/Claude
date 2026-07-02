"""Event-driven algo layer: quote events in, validated order intents out.

Everything in this package is shadow-only by default. Strategies are pure
functions of (event, context); the registry enforces intent validity and
downgrades any non-shadow intent unless governance has approved the cohort.
"""

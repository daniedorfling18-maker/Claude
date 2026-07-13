#!/usr/bin/env python3
"""WO-73 pre-amendment stub: proves only the fail-flat harness contract."""

from __future__ import annotations

import json
import os


NAMES = ("WO73_DUMMY_API_KEY", "WO73_DUMMY_API_SECRET", "WO73_DUMMY_PASSPHRASE")


def main() -> int:
    values = [os.getenv(name, "") for name in NAMES]
    if not all(values):
        status = "flat_missing_credentials"
        exit_code = 78
    elif any(not value.startswith("invalid-test-only-") for value in values):
        status = "flat_invalid_credentials"
        exit_code = 77
    else:
        status = "flat_invalid_credentials"
        exit_code = 77
    print(
        json.dumps(
            {
                "work_order": "WO-73",
                "status": status,
                "mode": "credential_fail_flat_stub",
                "open_orders": 0,
                "position_count": 0,
                "exposure_usd": 0.0,
                "attempted_order_actions": [],
                "halted": True,
                "owner_alert_eligible": True,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

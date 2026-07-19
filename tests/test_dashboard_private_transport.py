from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_dashboard_private_transport.py"
SPEC = importlib.util.spec_from_file_location("validate_dashboard_private_transport", MODULE_PATH)
assert SPEC and SPEC.loader
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def _status() -> dict:
    return json.loads(
        (ROOT / "tests" / "fixtures" / "tailscale_status_connected_sanitized.json").read_text(
            encoding="utf-8"
        )
    )


def _serve() -> str:
    return (
        "Available within your tailnet:\n"
        "https://polymarket-trader.example-tailnet.ts.net\n"
        "|-- / proxy http://127.0.0.1:8765\n"
    )


def test_private_https_transport_passes_sanitized_cli_shape() -> None:
    result = transport.evaluate_private_transport(
        tailscale_status=_status(),
        serve_status=_serve(),
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://polymarket-trader.example-tailnet.ts.net/",
        private_https_reachable=True,
    )

    assert result["status"] == "PASS"
    assert result["private_url"] == "https://polymarket-trader.example-tailnet.ts.net/"
    assert result["tailnet_authenticated"] is True
    assert result["public_listener_present"] is False
    assert result["serve_target_verified"] is True
    assert result["blockers"] == []
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_public_binding_fails_closed() -> None:
    result = transport.evaluate_private_transport(
        tailscale_status=_status(),
        serve_status=_serve(),
        docker_bindings="0.0.0.0:8765\n[::]:8765\n",
        expected_port=8765,
        configured_url="https://polymarket-trader.example-tailnet.ts.net/",
        private_https_reachable=True,
    )

    assert result["status"] == "FAIL"
    assert "dashboard_has_non_loopback_binding" in result["blockers"]
    assert result["public_listener_present"] is True


def test_logged_out_or_wrong_serve_target_fails_closed() -> None:
    status = _status()
    status["BackendState"] = "NeedsLogin"
    result = transport.evaluate_private_transport(
        tailscale_status=status,
        serve_status=_serve().replace("127.0.0.1:8765", "127.0.0.1:9999"),
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://polymarket-trader.example-tailnet.ts.net/",
        private_https_reachable=True,
    )

    assert result["status"] == "FAIL"
    assert set(result["blockers"]) >= {
        "tailscale_backend_not_running",
        "tailscale_serve_backend_target_mismatch",
    }


def test_funnel_or_non_tailnet_dns_fails_closed() -> None:
    status = _status()
    status["Self"]["DNSName"] = "dashboard.example.com"
    result = transport.evaluate_private_transport(
        tailscale_status=status,
        serve_status="Funnel on\nhttps://dashboard.example.com\n|-- / proxy http://127.0.0.1:8765\n",
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://dashboard.example.com/",
        private_https_reachable=True,
    )

    assert result["status"] == "FAIL"
    assert set(result["blockers"]) >= {
        "tailscale_https_dns_name_missing_or_invalid",
        "public_tailscale_funnel_enabled",
    }


def test_funnel_internet_status_fails_closed() -> None:
    result = transport.evaluate_private_transport(
        tailscale_status=_status(),
        serve_status=_serve().replace(
            "Available within your tailnet:", "Available on the internet:"
        ),
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://polymarket-trader.example-tailnet.ts.net/",
        private_https_reachable=True,
    )

    assert result["status"] == "FAIL"
    assert "public_tailscale_funnel_enabled" in result["blockers"]


def test_stale_configured_url_fails_closed() -> None:
    result = transport.evaluate_private_transport(
        tailscale_status=_status(),
        serve_status=_serve(),
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://old-host.example-tailnet.ts.net/",
        private_https_reachable=True,
    )

    assert result["status"] == "FAIL"
    assert "dashboard_env_private_url_missing_or_mismatch" in result["blockers"]


def test_static_configuration_without_live_https_probe_fails_closed() -> None:
    result = transport.evaluate_private_transport(
        tailscale_status=_status(),
        serve_status=_serve(),
        docker_bindings="127.0.0.1:8765\n",
        expected_port=8765,
        configured_url="https://polymarket-trader.example-tailnet.ts.net/",
        private_https_reachable=False,
    )

    assert result["status"] == "FAIL"
    assert result["private_https_reachable"] is False
    assert "private_https_reachability_not_proven" in result["blockers"]

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "preflight_vps_capacity.py"
SPEC = importlib.util.spec_from_file_location("preflight_vps_capacity", MODULE_PATH)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_compose_memory_limits_resolve_env_without_reading_unrelated_secrets(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n"
        "  live:\n"
        "    mem_limit: ${LIVE_LIMIT:-4g}\n"
        "  dashboard:\n"
        "    mem_limit: 256m\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("LIVE_LIMIT=5g\nSECRET_KEY=do-not-read\n", encoding="utf-8")

    limits = preflight.compose_memory_limits(compose, env_file)

    assert [(row["service"], row["bytes"]) for row in limits] == [
        ("live", 5 * preflight.GIB),
        ("dashboard", 256 * preflight.MIB),
    ]
    assert all("SECRET" not in str(row) for row in limits)


def test_underprovisioned_host_refuses_deploy_and_keeps_stack(tmp_path: Path) -> None:
    result = preflight.evaluate_capacity(
        {
            "vcpu_count": 1,
            "memory_total_bytes": 6 * preflight.GIB,
            "memory_available_bytes": 4 * preflight.GIB,
            "swap_total_bytes": 4 * preflight.GIB,
            "disk_total_bytes": 30 * preflight.GIB,
            "disk_free_bytes": 5 * preflight.GIB,
        },
        [
            {"service": "live", "bytes": 4 * preflight.GIB},
            {"service": "ops", "bytes": 2 * preflight.GIB},
            {"service": "watchdog", "bytes": 1 * preflight.GIB},
            {"service": "dashboard", "bytes": 256 * preflight.MIB},
        ],
    )

    assert result["status"] == "fail"
    assert result["decision"] == "REFUSE_DEPLOY_KEEP_EXISTING_STACK"
    assert result["blockers"] == ["vcpu_capacity", "compose_memory_commit", "deployment_disk_headroom"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_capacity_overrides_can_tighten_but_never_loosen_registered_floors() -> None:
    limits = [{"service": "only", "bytes": 1 * preflight.GIB}]
    metrics = {
        "vcpu_count": 2,
        "memory_total_bytes": 8 * preflight.GIB,
        "memory_available_bytes": 2 * preflight.GIB,
        "disk_total_bytes": 40 * preflight.GIB,
        "disk_free_bytes": 7 * preflight.GIB,
    }

    attempted_loosen = preflight.evaluate_capacity(
        metrics,
        limits,
        environment={"PM_VPS_MIN_VCPUS": "1", "PM_VPS_MIN_FREE_DISK": "1g"},
    )
    tightened = preflight.evaluate_capacity(
        metrics,
        limits,
        environment={"PM_VPS_MIN_VCPUS": "4", "PM_VPS_MIN_FREE_DISK": "8g"},
    )

    assert attempted_loosen["requirements"]["minimum_vcpus"] == 2
    assert attempted_loosen["requirements"]["minimum_free_disk_bytes"] == 6 * preflight.GIB
    assert attempted_loosen["status"] == "pass"
    assert tightened["status"] == "fail"
    assert tightened["blockers"] == ["vcpu_capacity", "deployment_disk_headroom"]


def test_real_compose_declares_memory_for_every_service() -> None:
    limits = preflight.compose_memory_limits(ROOT / "docker-compose.vps-paper.yml", ROOT / ".env.vps-paper.example")
    assert {row["service"] for row in limits} == {
        "polymarket-paper-live",
        "polymarket-dashboard",
        "superbru-auto-pick-watchdog",
        "vps-ops-scheduler",
        "vps-deploy-acceptance",
    }
    assert sum(row["bytes"] for row in limits) > 7 * preflight.GIB
    acceptance = next(row for row in limits if row["service"] == "vps-deploy-acceptance")
    assert acceptance["profiles"] == ["deploy-acceptance"]
    assert acceptance["capacity_replaces"] == ["vps-ops-scheduler"]

    result = preflight.evaluate_capacity(
        {
            "vcpu_count": 2,
            "memory_total_bytes": 8 * preflight.GIB,
            "memory_available_bytes": 2 * preflight.GIB,
            "disk_total_bytes": 40 * preflight.GIB,
            "disk_free_bytes": 8 * preflight.GIB,
        },
        limits,
    )
    modes = {row["mode"]: row for row in result["compose_memory_modes"]}
    expected_concurrent = int(7.25 * preflight.GIB)

    assert result["status"] == "pass"
    assert result["requirements"]["minimum_total_memory_bytes"] == expected_concurrent
    assert modes["default"]["bytes"] == expected_concurrent
    assert modes["profiles:deploy-acceptance"]["bytes"] == expected_concurrent
    assert modes["profiles:deploy-acceptance"]["replaced_services"] == [
        "vps-ops-scheduler"
    ]


def test_profile_replacement_must_name_a_declared_base_service() -> None:
    limits = [
        {"service": "live", "bytes": 4 * preflight.GIB},
        {
            "service": "acceptance",
            "bytes": 2 * preflight.GIB,
            "profiles": ["acceptance"],
            "capacity_replaces": ["missing-scheduler"],
        },
    ]

    try:
        preflight.compose_memory_modes(limits)
    except ValueError as exc:
        assert "unknown base services" in str(exc)
    else:
        raise AssertionError("unknown profile replacement must fail closed")

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

from polymarket_predictive_engine.credential_guard import scan_telemetry_credentials, telemetry_whitelist


REPO_ROOT = Path(__file__).resolve().parents[2]


def _guard_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "push_vps_telemetry.sh", repo / "scripts" / "push_vps_telemetry.sh")
    return repo


def test_repository_telemetry_and_archive_manifest_are_credential_clean() -> None:
    result = scan_telemetry_credentials(REPO_ROOT)

    assert result["status"] == "PASS", result["findings"]
    assert result["finding_count"] == 0
    assert result["matched_values_redacted"] is True
    assert set(result["telemetry_directories"]) == set(telemetry_whitelist(REPO_ROOT))
    assert "outputs/performance/ledger_state_archive_manifest.json" in result["archive_manifests"]


def test_guard_flags_sensitive_named_values_but_not_public_market_ids(tmp_path: Path) -> None:
    repo = _guard_repo(tmp_path)
    performance = repo / "outputs" / "performance"
    performance.mkdir(parents=True)
    private_value = "0x" + "f" * 64
    write_value = "sensitive-test-value-123"
    (performance / "sample.json").write_text(
        json.dumps(
            {
                "condition_id": "0x" + "a" * 64,
                "transaction_hash": "0x" + "b" * 64,
                "private_key": private_value,
                "api_secret": write_value,
            }
        ),
        encoding="utf-8",
    )

    result = scan_telemetry_credentials(repo)

    assert result["status"] == "FAIL"
    assert {row["kind"] for row in result["findings"]} == {"nonempty_sensitive_named_field"}
    rendered = json.dumps(result)
    assert private_value not in rendered
    assert write_value not in rendered


def test_guard_scans_csv_and_archive_manifest_and_redacts_matches(tmp_path: Path) -> None:
    repo = _guard_repo(tmp_path)
    ops = repo / "outputs" / "ops_scheduler"
    ops.mkdir(parents=True)
    (ops / "status.csv").write_text(
        "condition_id,passphrase\n0x" + "c" * 64 + ",planted-passphrase-value\n",
        encoding="utf-8",
    )
    manifest = repo / "outputs" / "performance" / "ledger_state_archive_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"files": [], "api_key": "planted-api-value"}), encoding="utf-8")

    result = scan_telemetry_credentials(repo)

    assert result["status"] == "FAIL"
    assert {row["path"] for row in result["findings"]} == {
        "outputs/ops_scheduler/status.csv",
        "outputs/performance/ledger_state_archive_manifest.json",
    }
    assert "planted-passphrase-value" not in json.dumps(result)
    assert "planted-api-value" not in json.dumps(result)


def test_guard_treats_api_and_access_tokens_as_credentials(tmp_path: Path) -> None:
    repo = _guard_repo(tmp_path)
    performance = repo / "outputs" / "performance"
    performance.mkdir(parents=True)
    secrets = {
        "api_token": "api-token-test-value-123",
        "access_token": "access-token-test-value-456",
        "refresh_token": "refresh-token-test-value-789",
    }
    (performance / "sample.json").write_text(json.dumps(secrets), encoding="utf-8")

    result = scan_telemetry_credentials(repo)

    assert result["status"] == "FAIL"
    assert result["finding_count"] == len(secrets)
    assert {row["kind"] for row in result["findings"]} == {
        "nonempty_sensitive_named_field"
    }
    rendered = json.dumps(result)
    assert all(value not in rendered for value in secrets.values())


def test_guard_classifies_public_hash_lists_but_rejects_credential_lists(tmp_path: Path) -> None:
    repo = _guard_repo(tmp_path)
    ops = repo / "outputs" / "ops_scheduler"
    ops.mkdir(parents=True)
    public_value = "a" * 64
    credential_value = "b" * 64
    (ops / "status.log").write_text(
        "\n".join(
            [
                '{"recent_top_markets": [',
                f'  "{public_value}"',
                "]}",
                '{"credentials": [',
                f'  "{credential_value}"',
                "]}",
            ]
        ),
        encoding="utf-8",
    )

    result = scan_telemetry_credentials(repo)

    assert result["status"] == "FAIL"
    assert result["finding_count"] == 1
    assert result["findings"][0]["location"] == "line:5"
    assert public_value not in json.dumps(result)
    assert credential_value not in json.dumps(result)


def test_telemetry_push_runs_guard_before_archive_or_snapshot_copy() -> None:
    text = (REPO_ROOT / "scripts" / "push_vps_telemetry.sh").read_text(encoding="utf-8")

    guard = text.index("check_telemetry_credential_guard.py")
    archive = text.index("push_vps_archive.sh")
    copy = text.index("copy_capped()")
    assert guard < archive < copy
    assert "refusing telemetry and archive push" in text
    assert "credential_guard.json" in text


def test_keyless_rotation_drill_stub_fails_flat_in_both_scenarios(tmp_path: Path) -> None:
    output = tmp_path / "credential_rotation_drill.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_executor_credential_fail_flat.py"),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["real_credentials_loaded"] is False
    assert result["post_amendment_item_4_implemented"] is False
    assert {row["scenario"] for row in result["scenarios"]} == {"missing", "invalid"}
    assert all(row["status"] == "PASS" for row in result["scenarios"])
    assert all(row["open_orders"] == 0 for row in result["scenarios"])
    assert all(row["exposure_usd"] == 0 for row in result["scenarios"])
    assert "invalid-test-only" not in output.read_text(encoding="utf-8")


def test_executor_onboarding_is_public_identifier_only_and_item4_stays_blocked() -> None:
    config = yaml.safe_load((REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    runbook = (REPO_ROOT / "docs" / "EXECUTOR_SUB_ACCOUNT_AND_CREDENTIAL_DRILL.md").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.vps-paper.yml").read_text(encoding="utf-8")

    assert config["maker_live_test"]["executor_wallet_address"] == ""
    assert "AUTO-REDEEM WINS" in runbook
    assert "operator and executor" in runbook
    assert "does not implement" in runbook
    assert "PM_EXECUTOR_API_KEY" not in compose
    assert "PM_EXECUTOR_API_SECRET" not in compose
    assert "PM_EXECUTOR_PASSPHRASE" not in compose

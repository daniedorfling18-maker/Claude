"""Mechanical S2/S4 coverage and mutation tests for WO-101."""

from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.engineering_standards_guard import (
    audit_s2,
    audit_s4,
    load_manifest,
    scan_direct_writes,
    scan_external_ingestion,
)


ROOT = Path(".")
PACKAGE = ROOT / "src" / "polymarket_predictive_engine"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "recorded" / "engineering_guard_manifest.json"


def test_repository_s2_s4_inventory_exactly_matches_reviewed_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert audit_s2(PACKAGE, manifest) == []
    assert audit_s4(PACKAGE, manifest, repository_root=ROOT) == []


def test_unregistered_direct_write_is_detected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "new_writer.py").write_text(
        "from pathlib import Path\n\ndef publish(path: Path):\n    path.write_text('partial')\n",
        encoding="utf-8",
    )
    sites = scan_direct_writes(package)
    assert sites == {"new_writer.py:publish:write_text": 1}
    errors = audit_s2(package, {"s2_direct_writes": {}})
    assert any("actual=1 expected=0" in error for error in errors)


def test_atomic_helper_is_not_misclassified_as_direct_write(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "safe_writer.py").write_text(
        "from .utils import write_text_atomic\n\ndef publish(path):\n    write_text_atomic(path, 'complete')\n",
        encoding="utf-8",
    )
    assert scan_direct_writes(package) == {}


def test_unregistered_external_calls_and_duplicate_count_are_detected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "new_parser.py").write_text(
        "import requests\n\ndef fetch():\n    requests.get('https://one.invalid')\n    requests.get('https://two.invalid')\n",
        encoding="utf-8",
    )
    assert scan_external_ingestion(package) == {"new_parser.py:fetch:requests.get": 2}

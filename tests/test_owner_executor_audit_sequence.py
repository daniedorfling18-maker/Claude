from pathlib import Path


def test_executor_code_audit_occurs_after_signing_but_before_merge() -> None:
    text = Path("docs/OWNER_CHECKS.md").read_text(encoding="utf-8")

    assert "Executor code is forbidden before signing" in text
    assert "After signing, before any executor merge or canary" in text
    assert "independent audit of the actual executor PR" in text

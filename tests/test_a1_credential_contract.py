from pathlib import Path


def test_a1_scopes_runtime_credentials_without_authorising_l1_or_wo67() -> None:
    text = Path("docs/KEY_CUSTODY_DESIGN_WO67_P5.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "only for scoped L2 CLOB" in normalized
    assert "withdrawal-capable L1 private key remains offline" in normalized
    assert "WO-67 stays blocked" in normalized
    assert "Nothing in A1 authorises funding or executor deployment" in normalized

from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows


def test_infers_clean_no_winner_from_gamma_payload():
    payload = {
        "slug": "sample-market",
        "conditionId": "0xabc",
        "question": "Sample?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.000001", "0.999999"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "closed": True,
        "active": True,
        "closedTime": "2026-01-01T00:00:00Z",
        "endDate": "2026-01-01T00:00:00Z",
    }
    rows, quality = infer_market_resolution_rows(payload)
    assert quality[0]["resolution_quality"] == "clean_settlement"
    assert quality[0]["condition_id"] == "0xabc"
    assert quality[0]["question"] == "Sample?"
    assert quality[0]["resolution_source"] == ""
    assert rows[0]["target"] == 0
    assert rows[1]["target"] == 1
    assert rows[1]["winning_token_id"] == "no-token"


def test_rejects_all_zero_closed_settlement_vector():
    payload = {
        "slug": "legacy-market",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0", "0"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "closed": True,
    }
    rows, quality = infer_market_resolution_rows(payload)
    assert quality[0]["resolution_quality"] == "missing_settlement_vector"
    assert rows[0]["target"] == ""
    assert rows[1]["target"] == ""


def test_keeps_active_market_unlabelled():
    payload = {
        "slug": "active-market",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.365", "0.635"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "closed": False,
        "active": True,
    }
    rows, quality = infer_market_resolution_rows(payload)
    assert quality[0]["resolution_quality"] == "unresolved_active"
    assert rows[0]["target"] == ""
    assert rows[1]["target"] == ""

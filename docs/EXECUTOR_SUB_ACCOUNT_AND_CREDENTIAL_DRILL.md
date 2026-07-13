# Executor sub-account onboarding and credential drill (WO-73)

This is an owner runbook, not execution authorisation. WO-67 and credential
loading remain blocked until the registered post-amendment conditions are met.

## Sub-account onboarding

1. Create a dedicated Polymarket executor account. Never reuse the operator
   account or its credentials.
2. In the executor account, open **Trading settings** and enable
   **AUTO-REDEEM WINS**. Record completion in the owner-controlled checklist;
   redemption logic is intentionally outside the future executor.
3. Put only the current registered ladder-stage capital in this account.
4. Add only its public profile/wallet identifier to
   `maker_live_test.executor_wallet_address`. This activates separate
   read-only scoreboard and reconciliation rows; it loads no credential.
5. Verify the dashboard and generated operating state show operator and executor
   wallets separately and `wallets_combined=false`.

## Pre-amendment fail-flat drill

Run the keyless stub contract:

```bash
python scripts/verify_executor_credential_fail_flat.py
```

Both missing and invalid dummy-credential scenarios must report `PASS`, zero
orders, zero positions, zero exposure, and a halted state. The result is
written to `outputs/execution/credential_rotation_drill.json`. No real key,
secret, passphrase, or `.env` value is read or persisted.

## Rotation/revocation procedure for the future executor

Only after the owner amendment and WO-67 implementation:

1. Start from zero open orders and independently verify the execution ledger.
2. Revoke the old L2 credentials at the venue.
3. Run the unchanged harness against the candidate executor with credentials
   absent, then invalid; both cases must fail flat and alert.
4. Derive replacement L2 credentials using the owner-offline process. Never
   put the L1 key, L2 credentials, or passphrase in GitHub, telemetry, chat,
   archives, config YAML, or command-line arguments.
5. Replace the owner-only VPS secret file atomically, preserve mode `0600`,
   restart only the future executor container, and repeat replay/canary
   certification before allowing any ladder stage.
6. Run `python scripts/check_telemetry_credential_guard.py`; any finding is a
   stop condition and must be treated as credential exposure.

The current repository does not implement step 4's runtime credential loading
and contains no executor order path.

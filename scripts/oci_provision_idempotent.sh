#!/usr/bin/env bash
# Idempotent, capacity-friendly single provisioning attempt (for CI / scheduled use).
#
# - If an instance with the display name already exists, do nothing (exit 0).
# - Otherwise make ONE launch attempt. "Out of capacity" is not a failure here: exit 0 so
#   the schedule simply retries next run. Real errors (auth / quota / bad OCID) exit non-zero
#   so the job fails visibly.
#
# Never creates a second VM, so it is safe to run on a frequent cron.
set -uo pipefail

command -v oci >/dev/null 2>&1 || { echo "OCI CLI not found." >&2; exit 127; }
: "${OCI_COMPARTMENT_ID:?set OCI_COMPARTMENT_ID}"
: "${OCI_SUBNET_ID:?set OCI_SUBNET_ID}"
: "${OCI_IMAGE_ID:?set OCI_IMAGE_ID}"

NAME="${OCI_DISPLAY_NAME:-polymarket-monitor}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

existing="$(oci compute instance list --compartment-id "$OCI_COMPARTMENT_ID" \
  --query "data[?\"display-name\"=='${NAME}' && \"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'].id | length(@)" \
  --raw-output 2>/dev/null || echo "0")"

if [ "${existing:-0}" != "0" ]; then
  echo "Instance '${NAME}' already exists (count=${existing}); nothing to do."
  exit 0
fi

OCI_MAX_TRIES="${OCI_MAX_TRIES:-1}" bash "${ROOT}/scripts/oci_launch_a1_retry.sh"
rc=$?
case "$rc" in
  0) echo "Provisioned '${NAME}'."; exit 0 ;;
  2) echo "No capacity this round; the schedule will retry."; exit 0 ;;
  *) echo "Provisioner failed (code ${rc})."; exit "$rc" ;;
esac

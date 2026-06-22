#!/usr/bin/env bash
# Retry-launch an Oracle Cloud instance until host capacity is available.
#
# The Always-Free A1.Flex (Ampere ARM) shape is frequently "Out of host capacity",
# especially on free-tier accounts and single-AD regions like Johannesburg. This script
# loops `oci compute instance launch`, retrying ONLY on capacity errors (cycling
# availability domains, backing off with jitter), and stops immediately on real errors
# (auth, quota/limit, bad OCIDs) so it never spins on a misconfiguration.
#
# Prerequisites: the OCI CLI installed and configured (`oci setup config`).
#
# Required env (OCIDs from the Oracle console):
#   OCI_COMPARTMENT_ID   tenancy or compartment OCID
#   OCI_SUBNET_ID        a public subnet OCID
#   OCI_IMAGE_ID         OS image OCID (Ubuntu 22.04 aarch64 for A1; x86_64 for E2)
#
# Optional env (with defaults):
#   OCI_SHAPE=VM.Standard.A1.Flex   (fallback: VM.Standard.E2.1.Micro, always available)
#   OCI_OCPUS=1  OCI_MEMORY_GB=6     (smaller A1 requests fit far more often than 4/24)
#   OCI_DISPLAY_NAME=polymarket-monitor   OCI_BOOT_VOLUME_GB=50
#   OCI_SSH_AUTHORIZED_KEYS_FILE=$HOME/.ssh/id_rsa.pub
#   OCI_AD / OCI_ADS                 pin one AD / list; default = auto-discover and cycle
#   OCI_RETRY_SLEEP_SECONDS=60  OCI_RETRY_SLEEP_MAX_SECONDS=180   (jittered backoff)
#   OCI_MAX_TRIES=0                  0 = retry forever
#   OCI_USER_DATA_FILE               optional cloud-init file to bootstrap the VM
#   OCI_RETRY_ALL_ERRORS=false       set true to also retry non-capacity errors
set -uo pipefail

command -v oci >/dev/null 2>&1 || { echo "OCI CLI not found. Install it and run 'oci setup config'." >&2; exit 127; }
: "${OCI_COMPARTMENT_ID:?set OCI_COMPARTMENT_ID}"
: "${OCI_SUBNET_ID:?set OCI_SUBNET_ID}"
: "${OCI_IMAGE_ID:?set OCI_IMAGE_ID}"

SHAPE="${OCI_SHAPE:-VM.Standard.A1.Flex}"
OCPUS="${OCI_OCPUS:-1}"
MEM_GB="${OCI_MEMORY_GB:-6}"
DISPLAY_NAME="${OCI_DISPLAY_NAME:-polymarket-monitor}"
BOOT_GB="${OCI_BOOT_VOLUME_GB:-50}"
SSH_KEY_FILE="${OCI_SSH_AUTHORIZED_KEYS_FILE:-$HOME/.ssh/id_rsa.pub}"
SLEEP_MIN="${OCI_RETRY_SLEEP_SECONDS:-60}"
SLEEP_MAX="${OCI_RETRY_SLEEP_MAX_SECONDS:-180}"
MAX_TRIES="${OCI_MAX_TRIES:-0}"

[ -f "$SSH_KEY_FILE" ] || { echo "SSH public key not found at $SSH_KEY_FILE (set OCI_SSH_AUTHORIZED_KEYS_FILE)." >&2; exit 1; }

# Availability domains: explicit override, else auto-discover and cycle.
if [ -n "${OCI_AD:-}" ]; then
  ADS_LIST="$OCI_AD"
elif [ -n "${OCI_ADS:-}" ]; then
  ADS_LIST="$(printf '%s' "$OCI_ADS" | tr ',' ' ')"
else
  ADS_LIST="$(oci iam availability-domain list --compartment-id "$OCI_COMPARTMENT_ID" --query 'data[].name' 2>/dev/null \
    | python3 -c "import sys,json; print(' '.join(json.load(sys.stdin)))" 2>/dev/null || true)"
fi
[ -n "$ADS_LIST" ] || { echo "Could not determine availability domains; set OCI_AD." >&2; exit 1; }

echo "Launching ${SHAPE} (${OCPUS} OCPU / ${MEM_GB}GB) as '${DISPLAY_NAME}'"
echo "Availability domains: ${ADS_LIST}"
echo "Retrying on capacity errors every ${SLEEP_MIN}-${SLEEP_MAX}s (max_tries=${MAX_TRIES:-inf}). Ctrl-C to stop."

try=0
while :; do
  for AD in $ADS_LIST; do
    try=$((try + 1))
    args=(compute instance launch
      --compartment-id "$OCI_COMPARTMENT_ID"
      --availability-domain "$AD"
      --shape "$SHAPE"
      --image-id "$OCI_IMAGE_ID"
      --subnet-id "$OCI_SUBNET_ID"
      --display-name "$DISPLAY_NAME"
      --assign-public-ip true
      --ssh-authorized-keys-file "$SSH_KEY_FILE"
      --boot-volume-size-in-gbs "$BOOT_GB"
      --wait-for-state RUNNING)
    case "$SHAPE" in
      *Flex) args+=(--shape-config "{\"ocpus\": ${OCPUS}, \"memoryInGBs\": ${MEM_GB}}") ;;
    esac
    [ -n "${OCI_USER_DATA_FILE:-}" ] && args+=(--user-data-file "$OCI_USER_DATA_FILE")

    echo "[$(date -u +%H:%M:%S)] try ${try}: launching in ${AD} ..."
    out="$(oci "${args[@]}" 2>&1)"
    rc=$?

    if [ $rc -eq 0 ]; then
      iid="$(printf '%s' "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])" 2>/dev/null || true)"
      ip="$(oci compute instance list-vnics --instance-id "$iid" --query 'data[0]."public-ip"' --raw-output 2>/dev/null || true)"
      echo ""
      echo "SUCCESS - instance RUNNING after ${try} attempt(s)."
      echo "  instance: ${iid:-<see console>}"
      echo "  public IP: ${ip:-<check console>}"
      [ -n "$ip" ] && echo "  ssh: ssh ubuntu@${ip}"
      exit 0
    fi

    if printf '%s' "$out" | grep -qiE "out of (host )?capacity"; then
      echo "  no capacity in ${AD}."
    elif printf '%s' "$out" | grep -qiE "limitexceeded|quota|already exists|service limit"; then
      echo "  STOP: quota/limit reached (you may already have a free instance):"; printf '%s\n' "$out" | tail -3; exit 1
    elif printf '%s' "$out" | grep -qiE "notauth|authenticat|401|403|can not be found|invalid"; then
      echo "  STOP: auth/config error - check OCIDs and run 'oci setup config':"; printf '%s\n' "$out" | tail -4; exit 1
    elif [ "${OCI_RETRY_ALL_ERRORS:-false}" = "true" ]; then
      echo "  non-capacity error (retrying as requested):"; printf '%s\n' "$out" | tail -2
    else
      echo "  STOP: unexpected error (set OCI_RETRY_ALL_ERRORS=true to keep trying):"; printf '%s\n' "$out" | tail -5; exit 1
    fi

    if [ "$MAX_TRIES" != "0" ] && [ "$try" -ge "$MAX_TRIES" ]; then
      echo "Reached OCI_MAX_TRIES=${MAX_TRIES} without capacity. Try later, a smaller shape, or PAYG."; exit 2
    fi
  done
  s=$(( SLEEP_MIN + (RANDOM % (SLEEP_MAX - SLEEP_MIN + 1)) ))
  echo "  capacity round exhausted; sleeping ${s}s ..."
  sleep "$s"
done

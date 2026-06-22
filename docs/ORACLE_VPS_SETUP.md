# Oracle VPS setup checklist

## Getting past "Out of host capacity" (A1.Flex)

Free-tier A1 (Ampere ARM) capacity is scarce, and Johannesburg is a single-AD region, so
"try another availability domain" does not help. Fastest fixes:

1. **Upgrade to Pay-As-You-Go.** A1 stays free within the Always-Free limits (up to 4 OCPU /
   24 GB), but PAYG accounts get capacity far more reliably. This is the biggest single fix.
2. **Request a smaller A1** (1 OCPU / 6 GB) — it fits far more often than 4/24, and the monitor
   only needs ~1 OCPU / 2 GB.
3. **Retry on a loop** — capacity frees up intermittently. Use the retry launcher:

```bash
oci setup config          # one-time: configure the OCI CLI
export OCI_COMPARTMENT_ID=ocid1.tenancy.oc1..xxxx
export OCI_SUBNET_ID=ocid1.subnet.oc1..xxxx
export OCI_IMAGE_ID=ocid1.image.oc1..xxxx      # Ubuntu 22.04 aarch64 for A1
export OCI_OCPUS=1 OCI_MEMORY_GB=6             # smaller request = better odds
bash scripts/oci_launch_a1_retry.sh
```

It retries only on capacity errors (cycling ADs, jittered backoff) and stops immediately on
auth/quota errors. Fallback shape if A1 stays unavailable: `OCI_SHAPE=VM.Standard.E2.1.Micro`
(x86, always available; 1 GB RAM, so add swap). Leave it running — it usually lands one within
hours. See the script header for all options (cloud-init bootstrap, max tries, sleep range).

## After the VM is provisioned

Use this once the Oracle Always Free VM is provisioned.

1. SSH into the Ubuntu VM.
2. Install Docker and Docker Compose.
3. Clone this repo.
4. Copy `.env.example` to `.env`.
5. Keep dry-run settings enabled.
6. Start the monitor with `docker compose -f docker-compose.monitor.yml up -d --build`.
7. Review logs with `docker compose -f docker-compose.monitor.yml logs -f polymarket-monitor`.

Do not commit `.env` or private keys.

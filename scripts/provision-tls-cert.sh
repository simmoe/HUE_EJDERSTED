#!/usr/bin/env bash
# Provision a publicly trusted TLS certificate for the hub.
#
# Preferred path (garden / Tailscale sites):
#   Tailscale issues a Let's Encrypt certificate for the machine's MagicDNS name.
#   Chrome trusts it with no interstitial and no "accept cert" dance.
#
# Requirements:
#   - Tailscale installed and logged in
#   - HTTPS Certificates enabled for the tailnet (admin console → DNS → HTTPS certs)
#   - Kiosk URL must use the MagicDNS hostname (not the raw LAN IP), otherwise
#     the certificate name will not match. Install Tailscale on the Android
#     kiosk (same tailnet + MagicDNS), or add a LAN DNS rewrite to the Pi.
#
# Usage (on the Pi):
#   ./scripts/provision-tls-cert.sh
#   sudo systemctl restart hue
#
# Optional override:
#   TS_CERT_HOSTNAME=kolonihave-pi.tailnet.ts.net ./scripts/provision-tls-cert.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT/certs}"
mkdir -p "$CERT_DIR"

if ! command -v tailscale >/dev/null 2>&1; then
  cat <<'EOF' >&2
tailscale is not installed on this machine.

Install Tailscale, enable HTTPS Certificates in the admin console, then re-run.
Alternatively use a custom domain with DNS-01 (certbot/Caddy) and place:
  certs/cert.pem
  certs/key.pem
EOF
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "tailscale is installed but not connected. Run: sudo tailscale up" >&2
  exit 1
fi

HOSTNAME="${TS_CERT_HOSTNAME:-}"
if [[ -z "$HOSTNAME" ]]; then
  HOSTNAME="$(
    tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("Self") or {}).get("DNSName") or "").rstrip("."))'
  )"
fi

if [[ -z "$HOSTNAME" ]]; then
  echo "Could not resolve Tailscale MagicDNS name. Set TS_CERT_HOSTNAME=..." >&2
  exit 1
fi

echo "→ Requesting Let's Encrypt cert via Tailscale for: $HOSTNAME"
umask 077
tmp_cert="$(mktemp "$CERT_DIR/cert.XXXXXX.pem")"
tmp_key="$(mktemp "$CERT_DIR/key.XXXXXX.pem")"
cleanup() { rm -f "$tmp_cert" "$tmp_key"; }
trap cleanup EXIT

# tailscale cert writes PEM files trusted by public browsers (Let's Encrypt).
if ! tailscale cert --cert-file "$tmp_cert" --key-file "$tmp_key" "$HOSTNAME"; then
  cat <<EOF >&2
tailscale cert failed.

Typical causes:
  1) HTTPS Certificates are disabled for the tailnet
     → https://login.tailscale.com/admin/dns  (enable HTTPS Certificates)
  2) This node is not allowed to issue certs yet (wait a minute after enabling)
  3) Clock skew on the Pi
EOF
  exit 1
fi

install -m 644 "$tmp_cert" "$CERT_DIR/cert.pem"
install -m 600 "$tmp_key" "$CERT_DIR/key.pem"
printf '%s\n' "https://${HOSTNAME}:8443" > "$CERT_DIR/public-url.txt"
chmod 644 "$CERT_DIR/public-url.txt"
trap - EXIT
rm -f "$tmp_cert" "$tmp_key"

echo "✓ Wrote $CERT_DIR/cert.pem and key.pem"
if command -v openssl >/dev/null 2>&1; then
  openssl x509 -in "$CERT_DIR/cert.pem" -noout -subject -issuer -dates 2>/dev/null || true
fi
echo
echo "Set the kiosk / HUB_PUBLIC_URL to:"
echo "  https://${HOSTNAME}:8443"
echo "Then: sudo systemctl restart hue"

# ADR 0001: One codebase, two runtime profiles

Status: Accepted

## Context

Ejderstedgade and the garden are separate physical kiosks with different
hardware and network boundaries. Permanent branches previously made it unclear
which code was live and allowed garden-only changes to drift away from home.

The garden owns a surveillance camera. Ejderstedgade must display that feed
without becoming a camera publisher or exposing either hub to the public
internet.

## Decision

Maintain one canonical `main` release line and deploy the same commit to both
hubs. Select behavior with validated runtime profiles:

- `home`: camera `viewer`; private internal HTTPS; read-only proxy to garden.
- `garden`: camera `publisher`; Tailscale MagicDNS HTTPS; owns snapshots,
  detection and evidence.

The home backend, rather than its browser, joins the cross-site data flow. It
proxies only camera reads over verified HTTPS. Publisher authorization is
restricted to configured garden kiosk source hosts.

## Consequences

- A release is identified by one Git commit and `/api/health` reports it.
- Profile and deploy target mismatches fail before files are copied.
- Device-specific secrets, addresses and credentials remain outside Git.
- Feature branches are short-lived; they are not deployment environments.
- Old branches remain available until both devices have been validated on the
  common release, after which cleanup requires explicit approval.

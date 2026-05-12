# Elfa webhook delivery (empirical)

The Elfa documentation has historically described a signed webhook scheme. In practice, Elfa Auto delivers webhooks **unsigned**, and the receiver in this project does not attempt verification.

## What Elfa actually sends

Captured by logging incoming headers on a real fire (most recently 2026-05-08):

```
host: <our-tunnel>.trycloudflare.com
user-agent: axios/1.13.2
content-type: application/json
content-length: <n>
accept: application/json, text/plain, */*
accept-encoding: gzip
x-auto-event-id: 2cd537c9-9cce-4ff8-935d-5cf269079899
x-auto-timestamp: 1777998536708
... (cloudflare proxy headers)
```

Notes:
- Timestamp header is `X-Auto-Timestamp` (13-digit milliseconds), NOT `X-Auto-Signature-Timestamp` and not seconds.
- No `X-Auto-Signature` header is sent. Webhooks are delivered UNSIGNED.

## Receiver behavior in this project

`src/elfa_grvt_bot/receiver.py` reads only `X-Auto-Event-Id` (required, used as the dedupe key) from inbound webhooks. There is no signature verification path; HMAC-related code was removed on 2026-05-08 because Elfa never reliably sent the signature header.

If Elfa ever turns signed delivery back on, a verifier would need to be reimplemented from current Elfa docs. The historical scheme they once described was: `signing_key = SHA256(secret); expected = HMAC_SHA256(signing_key, timestamp + "." + eventId + "." + rawBody)`, header `X-Auto-Signature: v1=<hex>`.

## Security implication

Webhook delivery is unsigned. Anyone who learns the public receiver URL can fire webhooks against it.

Mitigations in the project:
- Per-strategy `max_notional_usd` cap (last line of defense).
- Receiver `GRVT_ENV` must match strategy `env`.
- Strategy `status` must be `active` for the receiver to act on a fire (an attacker can only re-fire what already exists in the registry).
- Random trycloudflare URLs are not easily guessable.

If the URL leaks, an attacker can fire registered strategies up to the per-strategy cap. Rotate the tunnel and recreate strategies if that happens.

## Source IP and fingerprinting

Elfa Auto's outbound IP at the time of capture: `168.144.140.210`, location `sin20` (Cloudflare Singapore POP, since Cloudflare proxies the request). User agent `axios/1.13.2`.

Do not whitelist by IP; Cloudflare can rotate. The notional cap is the right defense given the unsigned delivery.

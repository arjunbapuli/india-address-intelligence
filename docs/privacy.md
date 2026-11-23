# Privacy and security

Addresses are personal data. Defaults are chosen so that nothing identifying is kept or sent anywhere unless you turn it on.

## What is stored

| Data | Default | Setting |
|---|---|---|
| Raw input / resolved address in `resolution_logs` | **Not stored** | `STORE_ADDRESS_TEXT=true` to enable |
| Resolution metadata (status, confidence, latency, PIN/district/state, input fingerprint) | Stored | `STORE_RESOLUTION_METADATA=false` to disable |
| Feedback text (transcript, corrected address) | Stored, because that is the point of feedback | Scrubbed after retention |
| Audio | Never stored | — |
| Retention | 30 days for logs and feedback text | `DATA_RETENTION_DAYS` (0 = keep) |

Purging runs hourly inside the API and can be run manually: `python -m scripts.purge_retention`.

## What is logged

Application logs are JSON on stdout and separate from user data in the database. They contain request ID, route, status, latency and hashed client ID. Addresses appear only as `<redacted len=.. fp=..>` (length plus a non-reversible fingerprint) unless `LOG_ADDRESS_TEXT=true`, which must never be set in production. The access log omits query strings, because search queries can contain addresses. Validation errors don't echo submitted values.

## What is sent to third parties

| Integration | Sends | Enabled when |
|---|---|---|
| LLM (OpenAI, Anthropic, Google, OpenAI-compatible) | The address text | `LLM_PROVIDER` set **and** `LLM_SEND_ADDRESS_DATA=true` |
| External geocoders (Nominatim, Photon, Geoapify) | The normalised address | `GEO_PROVIDERS` includes them **and** `GEO_SEND_ADDRESS_DATA=true` |
| STT (Deepgram, Whisper, Sarvam) | The audio | `STT_PROVIDER` set and the audio endpoint called |

With defaults, no address leaves the server. `/health` reports whether the LLM integration is sending address data. For on-premise or data-residency needs, use `LLM_PROVIDER=openai_compatible` with a self-hosted open model, and a self-hosted Nominatim/Photon.

## Security controls

- API keys via `API_KEYS`, compared in constant time; only a hashed key ID is logged or stored. Auth is mandatory in `ENVIRONMENT=production`.
- Per-key token-bucket rate limiting (429 + `Retry-After`).
- Request body limits: address ≤ 600 chars, batch ≤ 500, audio ≤ 10 MB.
- Errors never include stack traces or submitted values. Every error has a request ID for support.
- Container runs as a non-root user. Secrets come only from environment variables; `.env` is git-ignored.
- `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on responses.

## Production checklist

- [ ] `ENVIRONMENT=production`, strong `API_KEYS` (rotate by listing old and new keys together, then removing the old)
- [ ] `STORE_ADDRESS_TEXT=false` unless you have a documented purpose and consent
- [ ] Set `DATA_RETENTION_DAYS` to match your privacy policy
- [ ] TLS terminated by the platform (Render/Fly do this)
- [ ] Database not publicly reachable; backups encrypted
- [ ] Review DPDP Act 2023 obligations (purpose limitation, retention, processor agreements with any LLM/STT vendor)

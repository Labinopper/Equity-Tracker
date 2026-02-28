# Security Guide — Equity Tracker

This document covers the security model, authentication setup, and production deployment.

---

## Authentication

The app uses **TOTP-only authentication** (RFC 6238 — the same standard as Google Authenticator and 1Password). There is no password to remember. A 6-digit code that rotates every 30 seconds gates access to the entire web UI.

### How it works

1. You visit the app URL → redirected to the login page
2. Open 1Password, copy the 6-digit code for "Equity Tracker"
3. Enter the code → session cookie set (valid for 8 hours)
4. Access the full app; the cookie is refreshed on each login

### Initial setup

```bash
python equity_tracker/scripts/setup_totp.py
```

This generates a TOTP secret and prints:
- The `EQUITY_TOTP_SECRET=...` line to add to your `.env`
- The `otpauth://` URI to paste into 1Password

Add to 1Password:
1. New Item → One-Time Password
2. Paste the `otpauth://` URI into the OTP field
3. Save as "Equity Tracker"

Verify it works:
```bash
EQUITY_TOTP_SECRET=<your_value> python equity_tracker/scripts/setup_totp.py --verify
```

### Rotating the TOTP secret

If you lose access to your 1Password entry or need to reset:

```bash
python equity_tracker/scripts/setup_totp.py --reset
```

This generates a **new** secret. Update your `.env` and 1Password entry, then restart the server. The old secret stops working immediately.

The TOTP secret is **stable across restarts** — it is stored in the `EQUITY_TOTP_SECRET` environment variable and never auto-rotates.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `EQUITY_DB_PATH` | Yes | Absolute path to the `.db` file |
| `EQUITY_DB_PASSWORD` | Yes (encrypted) | SQLCipher passphrase |
| `EQUITY_DB_ENCRYPTED` | No (default: `true`) | Set `false` for plain SQLite dev mode |
| `EQUITY_TOTP_SECRET` | Yes | Base32 TOTP secret (from `equity_tracker/scripts/setup_totp.py`) |
| `EQUITY_SECRET_KEY` | Yes | Session signing key — generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `EQUITY_ALLOWED_ORIGINS` | Production | Your domain, e.g. `https://equity.yourdomain.com` |
| `EQUITY_DOCS_ENABLED` | No (default: `false`) | Set `true` only in dev to enable `/docs` |
| `EQUITY_DEV_MODE` | Dev only | Set `true` on localhost to allow cookies over plain HTTP |

Copy `.env.example` to `.env` and fill in your values. Never commit `.env` to version control.

---

## Database Encryption

The database is encrypted with **SQLCipher** using a key derived via **argon2id**:
- Time cost: 3, Memory: 64 MiB, Parallelism: 1
- Output: 32 bytes → 64-char hex key passed directly to SQLCipher (`PRAGMA key`)
- Salt: 16 random bytes stored in `{db_path}.salt`

The database passphrase is **never stored**. It is provided at startup via `EQUITY_DB_PASSWORD` and used only to derive the SQLCipher key. Even if someone obtains the `.db` file, they cannot read it without the passphrase.

---

## Session Security

Sessions are implemented as signed, timestamped cookies using `itsdangerous.TimestampSigner`:

| Attribute | Value |
|---|---|
| Cookie name | `eq_session` |
| Signature | HMAC-SHA1 via `itsdangerous` (keyed by `EQUITY_SECRET_KEY`) |
| Expiry | 8 hours |
| `HttpOnly` | Yes — not accessible to JavaScript |
| `Secure` | Yes in production (requires HTTPS), `false` if `EQUITY_DEV_MODE=true` |
| `SameSite` | `Lax` — sent on top-level navigation, blocked on cross-site requests |

To immediately invalidate all sessions, change `EQUITY_SECRET_KEY` and restart.

---

## Rate Limiting

| Endpoint | Limit |
|---|---|
| `POST /auth/login` | 5 attempts per 15 minutes per IP |
| `POST /admin/unlock` | 5 attempts per 15 minutes per IP |

Rate limiting is implemented with `slowapi`. Exceeding the limit returns HTTP 429.

---

## Security Headers

All responses include:

| Header | Value |
|---|---|
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' cdn.jsdelivr.net; style-src 'self' fonts.googleapis.com; font-src 'self' fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'` |

---

## Production Deployment

### HTTPS (required)

Use **Caddy** as a reverse proxy — it handles TLS certificates automatically:

```bash
# Install Caddy, then:
caddy run --config Caddyfile
```

Edit `Caddyfile` and replace `equity.yourdomain.com` with your domain. Caddy provisions a free Let's Encrypt certificate and redirects HTTP → HTTPS automatically.

### Docker

```bash
# Build
docker build -t equity-tracker .

# Run (with persistent volume for the database)
docker run -d \
  --name equity-tracker \
  -p 8000:8000 \
  -v equity-data:/data \
  --env-file .env \
  equity-tracker
```

The database file should be at `/data/portfolio.db` (set `EQUITY_DB_PATH=/data/portfolio.db`).

### Recommended stack

```
Internet → Caddy (HTTPS + reverse proxy) → uvicorn (port 8000) → FastAPI app
                                              └─ /data/portfolio.db (Docker volume)
```

---

## What Remains Public (No Authentication Required)

| Endpoint | Reason |
|---|---|
| `GET /health` | Reverse proxy health check |
| `GET /auth/login` | The login page itself |
| `POST /auth/login` | TOTP submission (rate-limited) |
| `POST /auth/logout` | Cookie deletion |
| `GET /admin/status` | Lock state probe (returns no personal data) |
| `POST /admin/unlock` | DB unlock (rate-limited; session not yet established) |

All other endpoints — including all data APIs and UI pages — require a valid session cookie.

---

## API Documentation

Swagger UI (`/docs`, `/redoc`, `/openapi.json`) is **disabled by default** in production.

To enable during local development only:
```bash
EQUITY_DOCS_ENABLED=true python run_api.py
```

---

## Audit Log

All database mutations (INSERT, UPDATE, DELETE) are recorded in an append-only `audit_log` table. The log captures: table name, record ID, action type, old and new values (JSON), notes, and timestamp.

To view: navigate to **Reports → Audit** in the web UI.

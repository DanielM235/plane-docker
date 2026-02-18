# Troubleshooting guide

This file covers the most common operational issues encountered after a fresh
Plane CE installation.

---

## Contents

1. [Authentication fails after God Mode setup (error 5065)](#1-authentication-fails-after-god-mode-setup-error-5065)
2. [HTTP vs HTTPS in `.env` — which is correct?](#2-http-vs-https-in-env--which-is-correct)
3. [Special characters (`$`, `!`) in passwords](#3-special-characters---in-passwords)
4. [SMTP / email not working](#4-smtp--email-not-working)
5. [Reset the admin password without email](#5-reset-the-admin-password-without-email)
6. [General debugging commands](#6-general-debugging-commands)

---

## 1. Authentication fails after God Mode setup (error 5065)

### Symptom

After completing the God Mode setup wizard at `/god-mode/`, signing in to the
main application at `/` fails with:

```
error_code=5065&error_message=AUTHENTICATION_FAILED_SIGN_IN
```

The API log shows `POST /auth/sign-in/ 302`.

### Root causes and fixes

#### 1a. `WEB_URL` protocol mismatch (most common)

Plane's Django backend uses `WEB_URL` to enforce CSRF and set cookie security
flags.  If your browser uses **HTTPS** but `WEB_URL` in `.env` is `http://…`,
the session cookie is not marked `Secure` and the browser refuses to send it
back over HTTPS — so every sign-in attempt fails silently.

**Diagnosis:**

Check whether nginx is serving TLS:

```bash
curl -vI https://your.domain.com/ 2>&1 | head -5
```

If you get a valid TLS handshake and your `.env` contains:

```
WEB_URL=http://your.domain.com
CORS_ALLOWED_ORIGINS=http://your.domain.com
```

…that is the cause.

**Fix:**

```bash
# On the server, edit .env:
sed -i \
  -e 's|^WEB_URL=http://|WEB_URL=https://|' \
  -e 's|^CORS_ALLOWED_ORIGINS=http://|CORS_ALLOWED_ORIGINS=https://|' \
  .env

# Restart the API (and all services that consume WEB_URL):
docker compose restart api worker beat-worker web space admin
```

Wait ~30 seconds for the health-checks to pass, then retry sign-in.

#### 1b. Account not fully activated (email verification pending)

Some Plane CE versions require email verification before a new account can
sign in.  If SMTP is broken the verification email is never delivered, and the
account stays in an unverified state.

**Diagnosis — check whether the account is active:**

```bash
docker exec -i $(docker ps -qf name=plane.*api) \
  python manage.py shell -c "
from plane.db.models import User
u = User.objects.get(email='your@email.com')
print('is_active:', u.is_active, '| is_email_verified:', getattr(u, 'is_email_verified', 'n/a'))
"
```

**Fix — force-activate the account:**

```bash
docker exec -i $(docker ps -qf name=plane.*api) \
  python manage.py shell -c "
from plane.db.models import User
u = User.objects.get(email='your@email.com')
u.is_active = True
# Plane >= 0.22 uses is_email_verified
if hasattr(u, 'is_email_verified'):
    u.is_email_verified = True
u.save()
print('done')
"
```

#### 1c. Password not set on the account

God Mode creates the instance admin but some versions send a magic link rather
than setting a password directly.  The account then exists with no usable
password.

**Diagnosis:**

```bash
docker exec -i $(docker ps -qf name=plane.*api) \
  python manage.py shell -c "
from plane.db.models import User
u = User.objects.get(email='your@email.com')
print('has_usable_password:', u.has_usable_password())
"
```

If it prints `False`, see [§5 — Reset the admin password](#5-reset-the-admin-password-without-email).

---

## 2. HTTP vs HTTPS in `.env` — which is correct?

**The rule: `WEB_URL` must match the protocol your users see in the browser
address bar.**

| nginx terminates TLS? | Browser URL | Correct `.env` value |
|---|---|---|
| No (plain HTTP) | `http://plane.example.com` | `WEB_URL=http://plane.example.com` |
| Yes (HTTPS) | `https://plane.example.com` | `WEB_URL=https://plane.example.com` |

The same protocol must be used for `CORS_ALLOWED_ORIGINS`.

`WEB_URL` controls:

- `NEXT_PUBLIC_API_BASE_URL` passed to all Next.js frontends.
- Django `CORS_ALLOWED_ORIGINS` (if derived from it).
- Django `CSRF_TRUSTED_ORIGINS`.
- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` flags (auto-set to `True`
  when the URL begins with `https`).

**After changing the protocol in `.env` you must restart every service:**

```bash
docker compose up -d --force-recreate
```

---

## 3. Special characters (`$`, `!`) in passwords

### In the browser UI (God Mode / sign-in forms)

`$` and `!` in passwords entered into browser forms are **completely safe**.
They are URL-encoded by the browser and decoded by Django before hashing.
There is no issue with using these characters in the Plane admin password.

### In `.env` file values — CRITICAL

Docker Compose performs shell-style variable substitution on values read from
`.env`.  A bare `$` in a value is interpreted as the start of a variable name.

| `.env` content | Value seen by the container |
|---|---|
| `PASSWORD=abc$def` | `abc` (or `abc<value-of-$def>`) — **WRONG** |
| `PASSWORD=abc$$def` | `abcdef` — still **WRONG** (doubled `$` collapses to one `$`) |
| `PASSWORD=abc\$def` | `abc$def` — **CORRECT** (escaped) |

**Wait — actually the safest approach:** avoid `$` in `.env` secrets entirely.
Use a generator that produces only alphanumeric or hex output.

Our `setup.sh install` generates all secrets with `python3 -c "import secrets, sys; sys.stdout.write(secrets.token_hex(50))"`, which yields only `[0-9a-f]` characters — no `$` possible.

**If you manually set a password in `.env` that contains `$`:**

1. Escape it with a backslash: `PASSWORD=abc\$def`
2. Or enclose the value in single quotes: `PASSWORD='abc$def'`
   *(Docker Compose strips the outer quotes.)*
3. Or replace `$` with a different character and regenerate.

**Verify what the container actually receives:**

```bash
docker exec $(docker ps -qf name=plane.*api) \
  env | grep POSTGRES_PASSWORD
```

Compare the output against what you expect.  If the value is truncated or
missing, the `$` is being expanded.

---

## 4. SMTP / email not working

### Diagnosis

Test the SMTP connection directly from the API container:

```bash
docker exec -i $(docker ps -qf name=plane.*api) \
  python manage.py shell -c "
from django.core.mail import send_mail
try:
    send_mail(
        subject='Plane SMTP test',
        message='If you receive this, SMTP is working.',
        from_email=None,          # uses EMAIL_FROM
        recipient_list=['your@email.com'],
        fail_silently=False,
    )
    print('OK — email sent')
except Exception as e:
    print('FAILED:', e)
"
```

Common errors and fixes:

| Error | Likely cause | Fix |
|---|---|---|
| `Connection refused` | Wrong `EMAIL_HOST` or `EMAIL_PORT` | Check `.env` values |
| `STARTTLS extension not supported` | Port 465 requires SSL, not STARTTLS | Set `EMAIL_USE_TLS=0` and `EMAIL_USE_SSL=1` |
| `Authentication failed` | Wrong `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | Check credentials |
| `[Errno -2] Name or service not known` | DNS resolution failing inside Docker | Verify `EMAIL_HOST` is reachable; try IP address |

### Temporarily switch to console backend (log emails instead of sending)

This lets you see email content in Docker logs without a working SMTP server:

```bash
# In .env:
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

docker compose restart api worker beat-worker
docker compose logs -f api
```

Email content will appear in the API container logs.  Switch back to
`django.core.mail.backends.smtp.EmailBackend` when SMTP is fixed.

### Common SMTP providers

| Provider | HOST | PORT | TLS | Notes |
|---|---|---|---|---|
| Gmail | `smtp.gmail.com` | `587` | `EMAIL_USE_TLS=1` | Requires App Password (2FA must be on) |
| Gmail (SSL) | `smtp.gmail.com` | `465` | `EMAIL_USE_SSL=1`, `EMAIL_USE_TLS=0` | |
| SendGrid | `smtp.sendgrid.net` | `587` | `EMAIL_USE_TLS=1` | User: `apikey`, pass: API key |
| Mailgun | `smtp.mailgun.org` | `587` | `EMAIL_USE_TLS=1` | |
| AWS SES | `email-smtp.<region>.amazonaws.com` | `587` | `EMAIL_USE_TLS=1` | SMTP credentials ≠ IAM credentials |

> **Note:** `EMAIL_USE_SSL` is not in `.env.example` because it defaults to `0`.
> If you need it, add `EMAIL_USE_SSL=1` to `.env` and make sure it is forwarded
> in `docker-compose.yml` under the `api` and `worker` environment blocks.

---

## 5. Reset the admin password without email

If SMTP is not working and you cannot receive a password reset link, reset
the password directly in the database via the Django management command.

### Option A — `changepassword` management command (interactive)

```bash
docker exec -it $(docker ps -qf name=plane.*api) \
  python manage.py changepassword your@email.com
```

You will be prompted twice for the new password.  The input is not echoed.

### Option B — Django shell (non-interactive / scripted)

```bash
docker exec -i $(docker ps -qf name=plane.*api) \
  python manage.py shell -c "
from plane.db.models import User
u = User.objects.get(email='your@email.com')
u.set_password('YourNewSecurePassword')
u.is_active = True
if hasattr(u, 'is_email_verified'):
    u.is_email_verified = True
u.save()
print('Password updated for', u.email)
"
```

After running either option, try signing in immediately — no restart needed.

> **Avoid `$` and `!` in passwords you set this way too**, just to keep things
> simple. The shell will expand `$` in single-quoted Python strings when passed
> via `-c`.  Use a password with letters, digits, and symbols like `@`, `#`,
> `%`, `^`, `&`, `*`, `(`, `)` instead.

---

## 6. General debugging commands

All commands assume you are in the `plane-docker` directory with `.env` loaded.

### View real-time logs

```bash
# All services:
docker compose logs -f

# Single service:
docker compose logs -f api
docker compose logs -f web
docker compose logs -f worker
```

### Check container health

```bash
docker compose ps
```

All services should show `healthy` or `running`.  If the `api` container is
`unhealthy`, the `web`, `space`, and `admin` frontends will refuse to start.

### Inspect environment variables the API container sees

```bash
docker exec $(docker ps -qf name=plane.*api) env | sort
```

Compare `WEB_URL`, `CORS_ALLOWED_ORIGINS`, `SECRET_KEY`, `DATABASE_URL`, and
`EMAIL_*` values against `.env`.

### Test the API health endpoint directly

```bash
# From the host, bypassing nginx:
curl -s http://127.0.0.1:${LISTEN_HTTP_PORT:-8080}/api/
# Expected: {"status":"ok"} or similar JSON
```

### Check database connectivity

```bash
docker exec $(docker ps -qf name=plane.*db) \
  psql -U plane -c '\l'
```

### Force a full restart

```bash
docker compose down
docker compose up -d
```

### Re-run migrations manually

```bash
docker compose run --rm migrator
```

This is safe to run at any time — migrations are idempotent.

### Rebuild with fresh images

```bash
./setup.sh pull          # pulls latest images for the current APP_RELEASE tag
docker compose up -d --force-recreate
```

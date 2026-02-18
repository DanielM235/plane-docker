# Environment Variables — Plane Community Edition

This document is the authoritative reference for every environment variable
consumed by this Docker Compose stack.  All variables are read from the
`.env` file at the root of the repository.

> **Community Edition only.**  This document intentionally omits variables
> that are exclusive to the commercial/Enterprise edition (AI models, SSO via
> SAML, advanced analytics, Intercom, …).  Only variables that work out of
> the box with the `stable` CE image are described.

---

## Table of contents

1. [How variables are loaded](#1-how-variables-are-loaded)
2. [Compose project](#2-compose-project)
3. [Image & deployment settings](#3-image--deployment-settings)
4. [Networking & public URL](#4-networking--public-url)
5. [Django application](#5-django-application)
6. [PostgreSQL](#6-postgresql)
7. [Redis](#7-redis)
8. [RabbitMQ](#8-rabbitmq)
9. [MinIO / S3 object storage](#9-minio--s3-object-storage)
10. [Gunicorn scaling](#10-gunicorn-scaling)
11. [Email (SMTP)](#11-email-smtp)
12. [OAuth integrations](#12-oauth-integrations)
13. [Caddy internal proxy](#13-caddy-internal-proxy)
14. [Quick-start checklist](#14-quick-start-checklist)

---

## 1. How variables are loaded

Docker Compose automatically reads the `.env` file located in the same
directory as `docker-compose.yml`.  Every variable defined there is
available for interpolation inside the Compose files with the `${VAR}`
syntax.

Variables are forwarded to containers **only when explicitly listed** in
the `environment:` section of each service.  Defining a variable in `.env`
does not automatically inject it into containers — it only makes it
available for Compose's own configuration-level substitution (image tags,
port numbers, volume names …).

```bash
# Bootstrap
cp .env.example .env
$EDITOR .env   # fill in all CHANGE_ME_ values
docker compose up -d
```

---

## 2. Compose project

### `COMPOSE_PROJECT_NAME`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `plane-prod` |
| **Used by** | Docker Compose (meta) |

A short identifier for this deployment.  Docker Compose prepends this value
to all **container names**, **volume names**, and **network names**.  The
`docker-compose.yml` `name:` field seeds this default so the project name
is always deterministic even before `.env` is created.

```
COMPOSE_PROJECT_NAME=plane-prod
# → containers:  plane-prod-api, plane-prod-db, plane-prod-proxy, …
# → volumes:     plane-prod_pgdata, plane-prod_redisdata, …
# → network:     plane-prod_backend
```

**Must be unique** across every Plane deployment on the host.  Use lowercase
letters, digits, and hyphens only.

```dotenv
# Production instance
COMPOSE_PROJECT_NAME=plane-prod

# Staging instance (different .env, same server)
COMPOSE_PROJECT_NAME=plane-staging

# Second tenant
COMPOSE_PROJECT_NAME=plane-team-abc
```

> **Warning:** Renaming an existing deployment by changing
> `COMPOSE_PROJECT_NAME` leaves the old volumes orphaned under the previous
> prefix.  Migrate data before renaming.

---

## 3. Image & deployment settings

### `APP_RELEASE`

| | |
|---|---|
| **Required** | No |
| **Default** | `stable` |
| **Used by** | All `makeplane/*` images |

Docker image tag applied to every Plane service image.  The `stable` tag
always resolves to the latest stable Community Edition release.  Pin to a
specific release for reproducible deployments:

```dotenv
APP_RELEASE=stable        # latest CE stable (recommended)
APP_RELEASE=v0.23.0       # pinned version
```

> All Plane services (`plane-backend`, `plane-frontend`, `plane-space`,
> `plane-admin`, `plane-live`, `plane-proxy`) share the same tag.  There
> is no per-service version pinning.

### `DOCKERHUB_USER`

| | |
|---|---|
| **Required** | No |
| **Default** | `makeplane` |
| **Used by** | All `makeplane/*` images |

Registry namespace prefix.  Override only when pulling from a private mirror
or air-gapped registry:

```dotenv
DOCKERHUB_USER=myregistry.example.com/plane-mirror
```

### `PULL_POLICY`

| | |
|---|---|
| **Required** | No |
| **Default** | `if_not_present` |
| **Used by** | All Plane service images |

Controls when Docker Compose pulls images.

| Value | Behaviour |
|-------|-----------|
| `always` | Pull on every `docker compose up` (ensures latest tag) |
| `if_not_present` | Pull only if the image is not in the local cache (faster cold starts) |
| `never` | Never pull (useful in air-gapped environments) |

---

## 4. Networking & public URL

These variables define how Plane knows its own public address and which
traffic origins to trust.

### `DOMAIN_NAME`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `plane.example.com` |
| **Used by** | nginx template (`NGINX_SERVER_NAME`) |

The **bare hostname** of the server, without protocol or path.  Used to
configure the nginx `server_name` directive.

```dotenv
DOMAIN_NAME=plane.example.com
```

### `WEB_URL`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `http://localhost` |
| **Used by** | `api`, `worker`, `web`, `space`, `admin` |

The **full publicly accessible base URL**, including protocol (and port if
non-standard).  Plane uses this value to:

- Generate absolute URLs in emails and API responses.
- Configure the Next.js `NEXT_PUBLIC_API_BASE_URL` environment variable so
  frontend pages know where to send API requests.
- Build the MinIO pre-signed URL base for file downloads.

```dotenv
WEB_URL=https://plane.example.com      # production with TLS
WEB_URL=http://plane.example.com       # if TLS is not yet configured
WEB_URL=http://localhost:9080          # local development
```

Must match `CORS_ALLOWED_ORIGINS`.  If they diverge, API calls from the
browser will be blocked by CORS.

### `CORS_ALLOWED_ORIGINS`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `http://localhost` |
| **Used by** | `api` |

A comma-separated list of origins that the Django REST framework will accept
cross-origin requests from.  In practice this is always the same value as
`WEB_URL`:

```dotenv
CORS_ALLOWED_ORIGINS=https://plane.example.com

# Multiple origins (e.g. during a domain migration):
CORS_ALLOWED_ORIGINS=https://plane.example.com,https://old.example.com
```

### `DEBUG`

| | |
|---|---|
| **Required** | No |
| **Default** | `0` |
| **Used by** | `api`, `worker`, `beat-worker`, `migrator` |

Django's `DEBUG` setting.  Set to `1` only in development environments.
In debug mode Django returns detailed tracebacks in HTTP responses (security
risk in production) and disables some performance optimisations.

```dotenv
DEBUG=0    # production
DEBUG=1    # development only
```

### `LISTEN_HTTP_PORT`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `8080` |
| **Used by** | `plane-proxy` (Docker port binding), nginx template |

The port on which the internal Caddy router (`plane-proxy`) binds on the
**host loopback interface** (`127.0.0.1`).  The system nginx reverse proxy
then forwards traffic from port 80/443 to this port.

Choose any free port on the server:

```dotenv
LISTEN_HTTP_PORT=8080    # default
LISTEN_HTTP_PORT=8091    # example for a second instance or custom config
```

The corresponding nginx `proxy_pass` directive must match:

```nginx
proxy_pass http://127.0.0.1:8091;
```

---

## 5. Django application

### `SECRET_KEY`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Default** | none |
| **Used by** | `api`, `worker`, `beat-worker`, `migrator` |

Django's cryptographic secret key.  Used for signing cookies, CSRF tokens,
password-reset links, and session data.  **Never reuse a key across
instances or environments.**

Generate a secure key:

```bash
python3 -c "import secrets; print(secrets.token_hex(50))"
```

```dotenv
SECRET_KEY=your_64_character_random_hex_string_here
```

Changing this value in a running instance will invalidate all existing
sessions, forcing all users to log in again.

---

## 6. PostgreSQL

All PostgreSQL settings are used to build the `DATABASE_URL` connection
string consumed by Django.  Individual host/port/user/password/db variables
are combined; you can alternatively supply `DATABASE_URL` directly to
override them all.

### `POSTGRES_HOST`

| | |
|---|---|
| **Default** | `plane-db` |
| **Used by** | `api`, `worker`, `beat-worker`, `migrator` |

Hostname of the PostgreSQL server.  Inside Docker this is the service name
(`plane-db`).  Change only if using an **external** PostgreSQL instance:

```dotenv
POSTGRES_HOST=plane-db                        # bundled container (default)
POSTGRES_HOST=db.internal.example.com        # external managed database
```

### `POSTGRES_PORT`

| | |
|---|---|
| **Default** | `5432` |

Standard PostgreSQL port.  Change only if the external database listens on a
non-standard port.

### `POSTGRES_USER`

| | |
|---|---|
| **Default** | `plane` |

Database user.  Used for both the `plane-db` container (`POSTGRES_USER`) and
the Django connection string.

### `POSTGRES_PASSWORD`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Default** | none |

Password for the PostgreSQL user.  Generate a strong random password:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### `POSTGRES_DB`

| | |
|---|---|
| **Default** | `plane` |

Name of the database.  The `migrator` container creates all tables in this
database on first run.

### `DATABASE_URL` _(optional override)_

If set, this full connection string takes precedence over all individual
`POSTGRES_*` variables.  Useful when connecting to a managed cloud database
with a non-standard DSN:

```dotenv
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

---

## 7. Redis

Redis is used as a **cache backend** and a **Celery result backend**.  All
services share the same Redis instance.

### `REDIS_HOST`

| | |
|---|---|
| **Default** | `plane-redis` |

Service name of the Redis container (or external hostname).

### `REDIS_PORT`

| | |
|---|---|
| **Default** | `6379` |

### `REDIS_URL` _(optional override)_

Override all individual Redis variables with a full connection string:

```dotenv
REDIS_URL=redis://:password@redis.internal.example.com:6379/0
```

---

## 8. RabbitMQ

RabbitMQ is the **message broker** for Celery — it carries tasks from `api`
to `worker` and `beat-worker`.

### `RABBITMQ_HOST`

| | |
|---|---|
| **Default** | `plane-mq` |

### `RABBITMQ_PORT`

| | |
|---|---|
| **Default** | `5672` |

AMQP protocol port.  (The RabbitMQ management UI on port 15672 is not
exposed to the host in this stack.)

### `RABBITMQ_DEFAULT_USER`

| | |
|---|---|
| **Default** | `plane` |

RabbitMQ admin username.  Provisioned automatically when the container first
starts.

### `RABBITMQ_DEFAULT_PASS`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Default** | none |

RabbitMQ admin password.

### `RABBITMQ_DEFAULT_VHOST`

| | |
|---|---|
| **Default** | `plane` |

RabbitMQ virtual host used to namespace this instance's queues.

### `AMQP_URL` _(optional override)_

Full AMQP connection string:

```dotenv
AMQP_URL=amqp://plane:password@plane-mq:5672/plane
```

---

## 9. MinIO / S3 object storage

Plane stores file attachments (issue attachments, project assets …) in an
S3-compatible bucket.  This stack ships MinIO as the storage backend.

Two sets of credentials exist because the Django backend uses the **AWS SDK
naming convention** (`AWS_*`), while MinIO itself uses its own names
(`MINIO_ROOT_*`).  They must point to the same credentials.

### `USE_MINIO`

| | |
|---|---|
| **Default** | `1` |
| **Used by** | `api`, `worker` |

Set to `1` to use MinIO (bundled) as the S3 backend.  Set to `0` if you are
using an **external S3-compatible service** (AWS S3, Backblaze B2, Cloudflare
R2 …) and configure the `AWS_*` variables to point at it instead.

### `MINIO_ROOT_USER`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Used by** | `plane-minio` container |

MinIO root username (equivalent to an AWS access key ID for the admin user).

### `MINIO_ROOT_PASSWORD`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Used by** | `plane-minio` container |

MinIO root password (equivalent to an AWS secret access key).  Minimum 8
characters.

### `MINIO_ENDPOINT_SSL`

| | |
|---|---|
| **Default** | `0` |
| **Used by** | `api` |

Set to `1` if the MinIO endpoint URL uses HTTPS.  With the bundled MinIO
container this is always `0` (plain HTTP inside Docker).  Set to `1` only
when using an external S3 endpoint that requires TLS.

### `AWS_ACCESS_KEY_ID`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Used by** | `api`, `worker` |

Must match `MINIO_ROOT_USER` when `USE_MINIO=1`.  When using external S3,
set this to the IAM user's access key.

### `AWS_SECRET_ACCESS_KEY`

| | |
|---|---|
| **Required** | **Yes — must be changed** |
| **Used by** | `api`, `worker` |

Must match `MINIO_ROOT_PASSWORD` when `USE_MINIO=1`.

### `AWS_REGION`

| | |
|---|---|
| **Default** | `us-east-1` |
| **Used by** | `api`, `worker` |

S3 region.  For MinIO this value is ignored by the server but must be a
non-empty string for the AWS SDK to initialise correctly.  For real AWS S3
set this to the actual region of your bucket (e.g. `eu-west-3`).

### `AWS_S3_BUCKET_NAME`

| | |
|---|---|
| **Default** | `uploads` |
| **Used by** | `api`, `worker` |

Name of the S3 bucket used to store Plane file uploads.  Plane's `migrator`
creates this bucket if it does not exist (when using MinIO).

### `AWS_S3_ENDPOINT_URL`

| | |
|---|---|
| **Default** | `http://plane-minio:9000` |
| **Used by** | `api`, `worker` |

The S3-API endpoint the Django backend uses to communicate with the storage
service.

- **Bundled MinIO:** `http://plane-minio:9000` (Docker DNS, HTTP).
- **External S3:** leave empty to use the AWS SDK default regional endpoint,
  or set the provider's specific endpoint URL.

### `BUCKET_NAME`

| | |
|---|---|
| **Default** | `uploads` |
| **Used by** | `plane-proxy` (Caddy) |

Name of the bucket as seen in the public URL path.  Caddy uses this to route
`/<BUCKET_NAME>/*` requests to MinIO.  Must match `AWS_S3_BUCKET_NAME`.

### `FILE_SIZE_LIMIT`

| | |
|---|---|
| **Default** | `5242880` (5 MB) |
| **Used by** | `api`, `worker`, `plane-proxy` |

Maximum allowed upload size in **bytes**.  Enforced at three levels:

1. Django rejects multipart uploads exceeding this size.
2. Caddy (`plane-proxy`) refuses request bodies larger than this value.
3. The nginx config sets `client_max_body_size` to 20 MB (hard ceiling above
   which nginx itself rejects the request before Caddy sees it).

Common values:

```dotenv
FILE_SIZE_LIMIT=5242880    #  5 MB (default)
FILE_SIZE_LIMIT=10485760   # 10 MB
FILE_SIZE_LIMIT=20971520   # 20 MB (also raise nginx client_max_body_size)
```

---

## 10. Gunicorn scaling

### `GUNICORN_WORKERS`

| | |
|---|---|
| **Default** | `2` |
| **Used by** | `api` |

Number of Gunicorn worker processes.  A common starting point is
`2 × vCPU_count + 1`.  Each worker handles one request at a time; with
the async Uvicorn worker class used by Plane, concurrent connections inside
one process are possible.

```dotenv
GUNICORN_WORKERS=3    # for a 1 vCPU server
GUNICORN_WORKERS=5    # for a 2 vCPU server
```

---

## 11. Email (SMTP)

Email is optional on first run but required for password reset, invitations,
and notification emails.

### `EMAIL_BACKEND`

| | |
|---|---|
| **Default** | `django.core.mail.backends.smtp.EmailBackend` |
| **Used by** | `api`, `worker` |

Django email backend class.  In development you can use the console backend
to print emails to stdout instead of sending them:

```dotenv
# Development: log emails to console
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

# Production: real SMTP
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
```

### `EMAIL_HOST`

| | |
|---|---|
| **Default** | _(empty)_ |
| **Used by** | `api`, `worker` |

SMTP server hostname.  Examples:

```dotenv
EMAIL_HOST=smtp.gmail.com
EMAIL_HOST=smtp.eu.mailgun.org
EMAIL_HOST=mail.example.com
```

### `EMAIL_PORT`

| | |
|---|---|
| **Default** | `587` |

SMTP port.  Common values:

| Port | Protocol |
|------|----------|
| 25 | SMTP (unauthenticated, often blocked) |
| 465 | SMTPS (implicit TLS — set `EMAIL_USE_TLS=0` and use Django's `EMAIL_USE_SSL=1` instead) |
| 587 | Submission (STARTTLS — default, recommended) |

### `EMAIL_HOST_USER`

| | |
|---|---|
| **Default** | _(empty)_ |

SMTP authentication username (usually an email address).

### `EMAIL_HOST_PASSWORD`

| | |
|---|---|
| **Default** | _(empty)_ |

SMTP authentication password or API key (e.g. an app-specific password for
Gmail).

### `EMAIL_USE_TLS`

| | |
|---|---|
| **Default** | `1` |

Set to `1` to use STARTTLS (recommended for port 587).  Set to `0` for
unencrypted SMTP or when using implicit TLS on port 465.

### `EMAIL_FROM`

| | |
|---|---|
| **Default** | `noreply@plane.example.com` |
| **Used by** | `api`, `worker` |

The `From:` address that appears on all outgoing emails.  Should be a
properly configured address that passes SPF/DKIM checks:

```dotenv
EMAIL_FROM=noreply@plane.example.com
EMAIL_FROM=Plane <noreply@plane.example.com>
```

---

## 12. OAuth integrations

All OAuth integrations are **optional**.  They add social login buttons to
the Plane sign-in page.  Leave variables empty to disable each integration.

Each provider requires registering an OAuth application in the provider's
developer console and obtaining a Client ID and Client Secret.  The callback
URL to register is always:

```
https://<DOMAIN_NAME>/auth/<provider>/callback/
```

### GitHub OAuth

| Variable | Description |
|----------|-------------|
| `GITHUB_CLIENT_ID` | OAuth App Client ID from github.com/settings/developers |
| `GITHUB_CLIENT_SECRET` | OAuth App Client Secret |

```dotenv
GITHUB_CLIENT_ID=Iv1.abc123def456
GITHUB_CLIENT_SECRET=secret_token_here
```

Once configured, the "Sign in with GitHub" button appears on the login page.

### GitLab OAuth

| Variable | Description |
|----------|-------------|
| `GITLAB_CLIENT_ID` | Application ID from your GitLab instance or gitlab.com |
| `GITLAB_CLIENT_SECRET` | Application Secret |

```dotenv
GITLAB_CLIENT_ID=abc123abc123
GITLAB_CLIENT_SECRET=secret_token_here
```

By default this authenticates against `gitlab.com`.  Self-hosted GitLab is
not configurable via environment variables in the CE image; it requires
adjusting the God Mode instance settings after the first login.

### Slack OAuth

| Variable | Description |
|----------|-------------|
| `SLACK_CLIENT_ID` | Client ID from api.slack.com/apps |
| `SLACK_CLIENT_SECRET` | Client Secret |

```dotenv
SLACK_CLIENT_ID=123456789012.123456789012
SLACK_CLIENT_SECRET=secret_token_here
```

Enables Slack notification integration at the workspace level.  Workspace
members link their Slack accounts in their profile settings.

---

## 13. Caddy internal proxy

These variables configure the `plane-proxy` container (Caddy).  They are
not visible to end-users.

### `CADDY_SITE_ADDRESS`

| | |
|---|---|
| **Required** | Yes |
| **Default** | `:80` |
| **Used by** | `plane-proxy` (as `SITE_ADDRESS` inside Caddy) |

The Caddy `SITE_ADDRESS` configuration key.  Must be set to `:80` for this
deployment (plain HTTP only — TLS is terminated by the system nginx).  This
value provides the site block's binding key in the Caddyfile; Caddy refuses
to start with an empty value.

```dotenv
CADDY_SITE_ADDRESS=:80     # always :80 in this setup
```

### `TRUSTED_PROXIES`

| | |
|---|---|
| **Default** | `0.0.0.0/0` |
| **Used by** | `plane-proxy` |

CIDR range of trusted reverse proxies from which Caddy will trust the
`X-Forwarded-For` and `X-Real-IP` headers.  The default (all IPs) is safe
because `plane-proxy` is never accessible from the public Internet — only
from the system nginx on loopback.

For a more restrictive setup:

```dotenv
TRUSTED_PROXIES=127.0.0.1/32    # trust loopback only
```

### `CERT_EMAIL`, `CERT_ACME_CA`, `CERT_ACME_DNS`

| Variable | Default | Purpose |
|----------|---------|---------|
| `CERT_EMAIL` | _(empty)_ | Email for Let's Encrypt ACME account |
| `CERT_ACME_CA` | `https://acme-v02.api.letsencrypt.org/directory` | ACME directory URL |
| `CERT_ACME_DNS` | _(empty)_ | DNS provider plugin config for DNS-01 challenges |

These variables are present in the Caddyfile but are **not used** in this
deployment because TLS is terminated by the system nginx.  They are provided
for completeness and must be left empty (or at their defaults).

> If you ever remove the system nginx and let Caddy handle TLS directly,
> set `CADDY_SITE_ADDRESS=https://plane.example.com` and configure
> `CERT_EMAIL` with a valid address.  You would also need to expose port 443
> from the `plane-proxy` container.

---

## 14. Quick-start checklist

The following variables **must** be set to non-default values before the
first `docker compose up`:

| Variable | Why |
|----------|-----|
| `COMPOSE_PROJECT_NAME` | Must be unique per instance to avoid container/volume name collisions |
| `SECRET_KEY` | Cryptographic baseline for all Django security features |
| `POSTGRES_PASSWORD` | Database access |
| `RABBITMQ_DEFAULT_PASS` | Message broker access |
| `MINIO_ROOT_USER` | Object storage admin username |
| `MINIO_ROOT_PASSWORD` | Object storage admin password |
| `AWS_ACCESS_KEY_ID` | Must equal `MINIO_ROOT_USER` when `USE_MINIO=1` |
| `AWS_SECRET_ACCESS_KEY` | Must equal `MINIO_ROOT_PASSWORD` when `USE_MINIO=1` |
| `DOMAIN_NAME` | nginx `server_name` and CORS |
| `WEB_URL` | Absolute URL base for links, CORS, Next.js API target |
| `CORS_ALLOWED_ORIGINS` | Browser API access control |
| `LISTEN_HTTP_PORT` | Port on which Caddy is reachable from nginx |

Variables with a `CHANGE_ME_` default in `.env.example` will cause the stack
to fail or be insecure if left unchanged.

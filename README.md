# plane-docker

**Production Docker Compose configuration for
[Plane Community Edition](https://github.com/makeplane/plane) (self-hosted).**

This repository packages Plane CE into a hardened, production-ready Docker
Compose stack designed for a **Debian 12 server** where **nginx is already
installed at the OS level** and acts as the external reverse proxy.

---

## Project version

This configuration wrapper is versioned independently from Plane itself.
The current version is stored in [`VERSION`](VERSION):

```bash
cat VERSION
```

Plane's own release tag is controlled by `APP_RELEASE` in `.env`
(default: `v1.2.3`).  Both versions together identify a deployment
unambiguously:

```
plane-docker 1.1.0  +  Plane CE v1.2.3  (migrate to v1.4.1 via ./setup.sh upgrade)
```

A stepwise, tested migration path between Plane releases is provided — see
[docs/MIGRATION.md](docs/MIGRATION.md).

---

## Architecture overview

```
Internet
  │
  ▼  :80 / :443
┌──────────────────────────────────────────────┐
│  System nginx  (Debian 12, /etc/nginx/)      │  TLS termination
│  virtual host: plane.example.com            │  security headers
└──────────────────┬───────────────────────────┘  WebSocket upgrade
                   │  proxy_pass→ 127.0.0.1:<LISTEN_HTTP_PORT>
                   ▼
┌──────────────────────────────────────────────┐
│  plane-proxy  (Caddy, Docker container)      │  path-based routing
│  loopback-only, never public                 │  /api/* → api:8000
└────┬─────────┬────────┬───────┬──────────────┘  /spaces/* → space:3000
     ▼         ▼        ▼       ▼                  /* → web:3000 …
   web       space    admin    api   live   minio
   (Docker internal network — not reachable from host)
```

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Prerequisites

| Requirement | Minimum version |
|-------------|----------------|
| Docker Engine | ≥ 24 |
| Docker Compose v2 plugin | ≥ 2.20 |
| System nginx | any recent (already installed on host) |
| Debian 12 host | (or compatible; Ubuntu 22.04 / 24.04 also work) |

```bash
# Verify
docker version
docker compose version
nginx -v
```

---

## Quick start

### 1 — Clone

```bash
git clone https://github.com/your-org/plane-docker.git
cd plane-docker
```

### 2 — Install

Run `install` once per instance.  It creates `.env` with auto-generated
secrets, applies your site-specific overrides, and pulls all images.
**No container is ever started by `install`** — that is intentional.  You
should inspect and adjust `.env` and configure nginx before the first
container runs.

```bash
./setup.sh install \
  --project-name plane-prod \
  --domain       plane.example.com \
  --port         8091
```

| Option | Short | What it sets in `.env` |
|--------|-------|------------------------|
| `--project-name NAME` | `-p` | `COMPOSE_PROJECT_NAME` — must be unique per instance |
| `--domain DOMAIN` | `-d` | `DOMAIN_NAME`, `WEB_URL`, `CORS_ALLOWED_ORIGINS` |
| `--port PORT` | `-P` | `LISTEN_HTTP_PORT` — the loopback port nginx proxies to |

All three options are optional; any value not supplied keeps the default
from `.env.example`.  All **secrets** (Django key, database/broker/MinIO
passwords) are auto-generated and written to `.env`.

> After installation, open `.env` to review every setting — especially
> email (SMTP) and `WEB_URL`/`CORS_ALLOWED_ORIGINS` if you plan to add TLS.  
> Full reference: [docs/ENV_VARS.md](docs/ENV_VARS.md)

### 3 — Configure system nginx

Expand the virtual-host template and enable it:

```bash
# On the Debian 12 host (not inside Docker):
export DOMAIN_NAME=plane.example.com
export LISTEN_HTTP_PORT=8091          # must match --port above

envsubst '${DOMAIN_NAME} ${LISTEN_HTTP_PORT}' \
  < nginx/templates/plane.conf.template \
  > /etc/nginx/sites-available/plane

ln -s /etc/nginx/sites-available/plane /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 4 — Start

Once you are satisfied with `.env` and nginx, start the stack:

```bash
./setup.sh start
```

`start` runs database migrations first, then brings the full stack up with
`docker compose up -d`.  It assumes `install` has already been completed.
Migrations are safe to rerun — they are idempotent.

### 5 — First login (God Mode)

Open `https://plane.example.com/god-mode/` in your browser.  Complete the
instance setup wizard to create the first admin account.

---

## Updating / migrating Plane

**Stepwise migration (recommended).**  Upgrade one release at a time, with a
backup before every step and the test suite run after every step:

```bash
./setup.sh backup            # safety net
./setup.sh upgrade           # v1.2.3 → v1.3.0 → v1.3.1 → v1.4.0 → v1.4.1
```

`upgrade` walks [`releases/order.txt`](releases/order.txt), backs up before
each step, applies the per-release override, pulls images, runs migrations,
restarts, and re-runs the test suite.  See
[docs/MIGRATION.md](docs/MIGRATION.md) for the full procedure and per-release
notes.

**Single-step manual update.**  If you only need to move the pinned tag
(not a full migration), update `APP_RELEASE` in `.env` and restart:

```bash
./setup.sh pull
./setup.sh restart
```

The `migrator` service runs automatically on `up -d` and applies any new
database migrations.  You can also run migrations explicitly:

```bash
./setup.sh migrate
```

> Re-running `install` is **not** needed for updates.  It is a one-time
> setup step.

---

## Backup & restore

```bash
./setup.sh backup                         # PostgreSQL dump + MinIO files
./setup.sh restore backups/<timestamp>    # restore database + files
```

Backups are stored under `BACKUP_DIR` (default `backups/`) with a manifest,
the database dump, the MinIO volume archive, and a copy of `.env`.  See
[docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for a full guide
covering:

- Authentication failures after God Mode setup (error 5065)
- HTTP vs HTTPS in `.env` — which protocol to use
- Special characters (`$`, `!`) in passwords and `.env` values
- SMTP not sending email (including per-provider settings)
- Resetting the admin password without email

---

## Running the test suite

Tests run entirely inside Docker against a separate nginx container that
mirrors the production proxy setup.  The suite covers startup, God Mode,
the REST API, object-storage reachability, WebSocket upgrade, the `setup.sh`
lifecycle, backup/restore, and the stepwise migration logic.

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run --rm --build test-runner
```

The suite also validates first-boot database initialisation (`test_11`): it
boots a fresh `postgres:15.7-alpine` container as the non-root `postgres`
user with all capabilities dropped, to prove the hardened `plane-db` service
can initialise a brand-new volume.  That test requires the Docker socket,
which is mounted into the test-runner by `docker-compose.test.yml`; it is
skipped automatically when the socket is not available.

---

## Multiple instances on the same host

Each instance needs:

1. A **unique `--project-name`** (and therefore `COMPOSE_PROJECT_NAME`):

   ```bash
   # First instance
   ./setup.sh install --project-name plane-prod --domain plane.example.com --port 8091

   # Second instance (different directory, different .env)
   ./setup.sh install --project-name plane-staging --domain staging.example.com --port 8092
   ```

2. A **unique `LISTEN_HTTP_PORT`** per instance — handled by `--port` above.
3. A **separate nginx server block** pointing to each port.

The `name:` field in `docker-compose.yml` seeds the project name from
`COMPOSE_PROJECT_NAME` automatically, so container/volume names never collide.

---

## TLS / HTTPS

TLS is terminated by the system nginx, **not** by the Caddy container.
Use Certbot with the Let's Encrypt nginx plugin on the host:

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d plane.example.com
```

Certbot patches `nginx/sites-available/plane` in place and sets up
automatic renewal.

---

## Project layout

```
plane-docker/
├── VERSION                      ← this config's version (not Plane's version)
├── CHANGES.md                   ← changelog of this configuration wrapper
├── .env.example                 ← template — copy to .env and fill in secrets
├── docker-compose.yml           ← production stack
├── docker-compose.test.yml      ← test overlay (nginx mirror + pytest runner)
├── setup.sh                     ← bootstrap, backup/restore, and migration script
├── releases/
│   ├── order.txt                ← canonical migration order (oldest → newest)
│   └── vX.Y.Z.env               ← per-release configuration overrides
├── nginx/
│   ├── nginx.conf               ← system nginx reference configuration
│   └── templates/
│       └── plane.conf.template  ← virtual-host template (envsubst)
├── docs/
│   ├── ARCHITECTURE.md          ← service topology, proxy chain, port guide
│   ├── ENV_VARS.md              ← complete environment-variable reference
│   ├── MIGRATION.md             ← stepwise migration guide
│   ├── BACKUP_RESTORE.md        ← backup & restore procedure
│   ├── SECRET_ROTATION.md       ← safe secret-rotation procedure
│   ├── SECURITY_AUDIT.md        ← docker-compose security audit
│   └── releases/                ← per-release migration notes
└── tests/
    ├── conftest.py              ← pytest fixtures and wait helpers
    ├── support.py               ← shared fake-docker helpers
    ├── Dockerfile.test          ← test-runner image
    ├── requirements.txt         ← Python test dependencies
    └── test_0{1..9}_*.py        ← startup, API, storage, WS, setup, backup, migration
```

---

## Useful commands

```bash
# First-time setup (creates .env, pulls images, runs migrations)
./setup.sh install --project-name plane-prod --domain plane.example.com --port 8091

# Start / stop / restart
./setup.sh start
./setup.sh stop
./setup.sh restart

# Status of all services
./setup.sh status

# Tail logs from the API
docker compose logs -f api

# One-off Django management command
docker compose run --rm api python manage.py shell

# Backup PostgreSQL + uploaded files (MinIO)
./setup.sh backup

# Stepwise migration to the latest release
./setup.sh upgrade

# Show this config's version
cat VERSION

# Show running Plane image tags
docker compose images
```

---

## References

- [Plane architecture docs](https://developers.plane.so/self-hosting/plane-architecture)
- [Plane environment variables](https://developers.plane.so/self-hosting/govern/environment-variables)
- [Plane Docker Compose guide](https://developers.plane.so/self-hosting/methods/docker-compose)
- [External reverse proxy guide](https://developers.plane.so/self-hosting/govern/reverse-proxy)

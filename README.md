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
(default: `stable`).  Both versions together identify a deployment
unambiguously:

```
plane-docker 1.0.0  +  Plane CE stable (e.g. v0.23.0)
```

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

## Updating Plane

1. Update `APP_RELEASE` in `.env` if you want to pin to a specific version
   (leave as `stable` to always get the latest stable release).
2. Pull new images and restart:

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
mirrors the production proxy setup.  26 tests cover startup, God Mode,
the REST API, object-storage reachability, and WebSocket upgrade.

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run --rm --build test-runner
```

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
├── .env.example                 ← template — copy to .env and fill in secrets
├── docker-compose.yml           ← production stack
├── docker-compose.test.yml      ← test overlay (nginx mirror + pytest runner)
├── setup.sh                     ← bootstrap script
├── nginx/
│   ├── nginx.conf               ← system nginx reference configuration
│   └── templates/
│       └── plane.conf.template  ← virtual-host template (envsubst)
├── docs/
│   ├── ARCHITECTURE.md          ← service topology, proxy chain, port guide
│   └── ENV_VARS.md              ← complete environment-variable reference
└── tests/
    ├── conftest.py              ← pytest fixtures and wait helpers
    ├── Dockerfile.test          ← test-runner image
    ├── requirements.txt         ← Python test dependencies
    └── test_0{1..5}_*.py        ← 26 tests covering all major subsystems
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

# Backup PostgreSQL
docker compose exec plane-db pg_dump -U plane plane > backup.sql

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

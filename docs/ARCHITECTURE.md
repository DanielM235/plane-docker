# Architecture — Plane Community Edition (self-hosted)

This document describes the full service topology of this Docker Compose
deployment, with a particular focus on the **reverse-proxy chain** and how
to map a public domain to the stack.

---

## Table of contents

1. [Service topology](#1-service-topology)
2. [Reverse-proxy chain](#2-reverse-proxy-chain)
3. [Internal Docker network](#3-internal-docker-network)
4. [Configuring for a custom domain and port](#4-configuring-for-a-custom-domain-and-port)
5. [TLS / HTTPS](#5-tls--https)
6. [Ports summary](#6-ports-summary)

---

## 1. Service topology

The stack is composed of three distinct layers:

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — Public edge (system nginx, Debian 12 OS level)           │
│  Listens on :80 / :443.  Handles TLS and security headers.         │
└────────────────────────────┬────────────────────────────────────────┘
                             │ proxy_pass http://127.0.0.1:<LISTEN_HTTP_PORT>
┌────────────────────────────▼────────────────────────────────────────┐
│  LAYER 2 — Internal router (plane-proxy, Caddy)                     │
│  Docker container.  Routes by URL path to individual services.      │
│  Bound on the host as 127.0.0.1:<LISTEN_HTTP_PORT> (loopback only). │
└──┬───────────┬────────────┬──────────┬──────────┬───────────────────┘
   │           │            │          │          │   (Docker backend network)
   ▼           ▼            ▼          ▼          ▼
 web:3000  space:3000  admin:3000  api:8000   live:3000   plane-minio:9000
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — Application services (all on Docker internal network)    │
│  Never reachable directly from the host or the Internet.            │
└─────────────────────────────────────────────────────────────────────┘
```

### Service catalogue

| Service | Image | Role |
|---------|-------|------|
| `api` | `makeplane/plane-backend` | Django REST API served by Gunicorn/Uvicorn. All data operations, authentication, and business logic. |
| `worker` | `makeplane/plane-backend` | Celery worker — processes asynchronous tasks (notifications, exports …). |
| `beat-worker` | `makeplane/plane-backend` | Celery Beat — triggers scheduled/periodic tasks. |
| `migrator` | `makeplane/plane-backend` | One-shot init container that runs Django `migrate` then exits. Starts before `api`. |
| `web` | `makeplane/plane-frontend` | Next.js main application (workspaces, issues, projects …). Served on `:3000`. |
| `space` | `makeplane/plane-space` | Public project-sharing views (`/spaces/`). Separate Next.js app on `:3000`. |
| `admin` | `makeplane/plane-admin` | Instance-administration dashboard (`/god-mode/`). Separate Next.js app on `:3000`. |
| `live` | `makeplane/plane-live` | Node.js real-time collaboration service — WebSocket endpoint at `/live/`. |
| `plane-proxy` | `makeplane/plane-proxy` | Caddy-based internal path-router. **The only container that exposes a host port.** |
| `plane-db` | `postgres:15` | PostgreSQL — primary relational store. |
| `plane-redis` | `redis:7` | Redis — cache, session store, Celery result backend. |
| `plane-mq` | `rabbitmq:3.13` | RabbitMQ — Celery task broker. |
| `plane-minio` | `minio/minio` | S3-compatible object storage for attachments and assets. |

---

## 2. Reverse-proxy chain

### 2.1 System nginx (Layer 1)

The **system-level nginx** (installed via `apt` on Debian 12) is the sole
public entry point.  It:

- Listens on port **80** (HTTP) and **443** (HTTPS after TLS setup).
- Terminates TLS (certificates managed externally, e.g. Certbot / Let's
  Encrypt).
- Adds security headers (`X-Content-Type-Options`, `X-Frame-Options` …).
- Forwards the `Upgrade`/`Connection` headers required for WebSocket.
- Proxies all traffic to the internal Caddy container via loopback:
  `proxy_pass http://127.0.0.1:<LISTEN_HTTP_PORT>`.

The site configuration template lives in
`nginx/templates/plane.conf.template`.  It uses two variables:

| Variable | Description |
|----------|-------------|
| `NGINX_SERVER_NAME` | Public hostname — e.g. `plane.example.com` |
| `NGINX_PROXY_PASS` | Caddy loopback target — e.g. `http://127.0.0.1:8080` |

### 2.2 Caddy internal router (Layer 2 — `plane-proxy`)

The `plane-proxy` container runs Caddy configured by Plane's own `Caddyfile`.
It routes incoming requests **by path prefix** to the upstream Docker service:

| URL pattern | Upstream |
|-------------|----------|
| `/spaces/*` | `space:3000` |
| `/god-mode/*` | `admin:3000` |
| `/api/*` | `api:8000` |
| `/auth/*` | `api:8000` |
| `/live/*` | `live:3000` |
| `/<BUCKET_NAME>/*` | `plane-minio:9000` |
| `/*` (catch-all) | `web:3000` |

Caddy also enforces `client_max_body_size` (via `FILE_SIZE_LIMIT`) and
forwards real-IP headers to the application tier.

> **Important:** Caddy is configured with `SITE_ADDRESS=:80` in this
> deployment. It **does not** terminate TLS. The system nginx handles HTTPS.

### 2.3 Full request lifecycle

```
Browser → nginx :443 (TLS termination)
       → proxy_pass → 127.0.0.1:8080 (or whichever LISTEN_HTTP_PORT)
       → Caddy :80 (path routing)
       → api:8000       (for /api/* and /auth/*)
          │
          ├── plane-db:5432   (PostgreSQL reads/writes)
          ├── plane-redis:6379 (cache / sessions)
          └── plane-mq:5672   (async task dispatch)
               └── worker / beat-worker (Celery consumers)
```

WebSocket path:
```
Browser → nginx :443 (Upgrade/Connection forwarded)
       → Caddy :80 (Upgrade forwarded)
       → live:3000 (WebSocket server)
```

---

## 3. Internal Docker network

All containers (except `migrator` which terminates) share a single bridge
network called `backend` (prefixed with `COMPOSE_PROJECT_NAME`).

- Services communicate using **Docker DNS names** (`api`, `plane-db`,
  `plane-redis`, etc.).
- No service port is exposed to the host **except** `plane-proxy`, which
  binds only on the loopback interface (`127.0.0.1:<LISTEN_HTTP_PORT>`).
- External traffic can only enter through nginx → Caddy.

---

## 4. Configuring for a custom domain and port

### Example: map `plane.example.com` → port `8091`

#### Step 1 — `.env` file

```dotenv
DOMAIN_NAME=plane.example.com
WEB_URL=https://plane.example.com          # or http:// if no TLS yet
CORS_ALLOWED_ORIGINS=https://plane.example.com

# Port the Caddy container exposes on the host loopback:
LISTEN_HTTP_PORT=8091

# Caddy internal site address (always plain HTTP inside Docker):
CADDY_SITE_ADDRESS=:80
```

#### Step 2 — Render the nginx site block

```bash
export NGINX_SERVER_NAME=plane.example.com
export NGINX_PROXY_PASS=http://127.0.0.1:8091

envsubst '${NGINX_SERVER_NAME} ${NGINX_PROXY_PASS}' \
  < nginx/templates/plane.conf.template \
  > /etc/nginx/sites-available/plane

sudo ln -sf /etc/nginx/sites-available/plane \
            /etc/nginx/sites-enabled/plane
sudo nginx -t && sudo systemctl reload nginx
```

#### Step 3 — Start the stack

```bash
docker compose up -d
```

#### What happens at runtime

```
Client: GET https://plane.example.com/
  → nginx :443  (server_name plane.example.com)
    → proxy_pass http://127.0.0.1:8091
      → plane-proxy (Caddy) :80
        → web:3000  (catch-all, Next.js SPA)
```

```
Client: GET https://plane.example.com/api/instances/
  → nginx :443
    → 127.0.0.1:8091
      → Caddy /api/* → api:8000
        → PostgreSQL / Redis / MinIO (internal)
```

### Multiple Plane instances on the same host

Because every container name, volume, and network is prefixed with
`COMPOSE_PROJECT_NAME`, you can run two fully isolated instances:

```
Instance A:  COMPOSE_PROJECT_NAME=plane-prod   LISTEN_HTTP_PORT=8091
Instance B:  COMPOSE_PROJECT_NAME=plane-staging LISTEN_HTTP_PORT=8092
```

Each gets its own nginx `server_name` block pointing to its own port.

---

## 5. TLS / HTTPS

TLS is **always terminated by the system nginx**, never by Caddy.  Inside
Docker, all traffic is plain HTTP.

Recommended TLS setup with Certbot on Debian 12:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d plane.example.com
```

Certbot rewrites the nginx site block to redirect HTTP → HTTPS and adds the
`ssl_certificate` / `ssl_certificate_key` directives automatically.

After obtaining a certificate, update `.env`:

```dotenv
WEB_URL=https://plane.example.com
CORS_ALLOWED_ORIGINS=https://plane.example.com
```

Then restart the stack so the Django backend generates correct absolute URLs:

```bash
docker compose up -d --force-recreate api worker beat-worker
```

---

## 6. Ports summary

| Port | Where | Exposed to | Service |
|------|-------|------------|---------|
| 80 | Host OS | Public Internet | System nginx (HTTP — redirects to HTTPS) |
| 443 | Host OS | Public Internet | System nginx (HTTPS) |
| `LISTEN_HTTP_PORT` (e.g. 8091) | Host loopback (`127.0.0.1`) | nginx only | `plane-proxy` (Caddy) |
| 80 (container) | Docker network | `plane-proxy` only | `web`, `space`, `admin`, `live`, `api` via Caddy routing |
| 8000 (container) | Docker network | `plane-proxy` only | `api` (Gunicorn) |
| 3000 (container) | Docker network | `plane-proxy` only | `web`, `space`, `admin`, `live` (Node/nginx) |
| 5432 (container) | Docker network | `api`, `worker`, `beat-worker`, `migrator` | `plane-db` (PostgreSQL) |
| 6379 (container) | Docker network | `api`, `worker`, `beat-worker` | `plane-redis` (Redis) |
| 5672 (container) | Docker network | `api`, `worker`, `beat-worker` | `plane-mq` (RabbitMQ AMQP) |
| 9000 (container) | Docker network | `plane-proxy`, `api`, `worker` | `plane-minio` (MinIO S3 API) |
| 9001 (container) | Docker network | not exposed | `plane-minio` (MinIO web console) |

> **Security note:** Only ports 80, 443, and `LISTEN_HTTP_PORT` are ever
> reachable from outside the host.  All other ports are confined inside the
> Docker `backend` bridge network.

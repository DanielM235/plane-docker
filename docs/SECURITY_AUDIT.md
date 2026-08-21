# Security audit — docker-compose.yml

This document records the security review of the Compose stack and the
disposition of each finding.  It is updated whenever the stack is audited.

## Summary

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | `live` missing `LIVE_SERVER_SECRET_KEY` → crash-loop at 100% CPU | High | **Fixed** |
| 2 | `live` missing `REDIS_URL` → could not reach Redis | High | **Fixed** |
| 3 | No healthcheck on `worker`, `beat-worker`, `live` | Medium | **Fixed** |
| 4 | No CPU/memory/pids limits → runaway process exhaustion | High | **Fixed** |
| 5 | Backend/worker containers run as root | Medium | Open (upstream constraint) |
| 6 | `minio/minio:latest` unpinned | Medium | Open (recommendation) |
| 7 | `TRUSTED_PROXIES` default `0.0.0.0/0` | Low | Acceptable (documented) |
| 8 | `read_only: true` not applied broadly | Low | Open (upstream constraint) |

---

## Findings

### 1 & 2 — `live` missing required environment variables

- **Symptom:** the `live` service failed its startup environment validation
  (`LIVE_SERVER_SECRET_KEY` required) and looped, pinning a CPU core; after
  adding the key, it could not find Redis.
- **Fix:** the `live` service now receives `LIVE_SERVER_SECRET_KEY`,
  `CORS_ALLOWED_ORIGINS`, `REDIS_HOST`, `REDIS_PORT` and `REDIS_URL`, and the
  backend services receive the same `LIVE_SERVER_SECRET_KEY` (they must
  match).  `live` now also depends on `plane-redis` being healthy.

### 3 — Missing healthchecks

- **Fix:** `worker` and `beat-worker` now check the Celery process is alive
  (`pgrep -x celery`); `live` checks its Node server is listening on port 3000
  (`nc -z 127.0.0.1 3000`).  All other services already had healthchecks.

### 4 — No resource limits

- **Fix:** every service now has `deploy.resources.limits` with a CPU cap, a
  memory cap and a `pids` limit (fork-bomb protection).  Limits are tunable
  through `*_CPU_LIMIT` / `*_MEM_LIMIT` environment variables (see
  `docs/ENV_VARS.md`).  A spinning process can no longer consume more than its
  allotted CPU and will eventually be killed by its memory/pids ceiling.

### 5 — Containers run as root (open)

The backend images run their processes as `root` (Celery logs a
"superuser privileges" warning).  This is inherited from the upstream
`makeplane/plane-backend` image.  Running as a non-root `user:` is possible
only if the upstream image is built to support it; enabling it unvalidated can
break the API/worker.  **Recommendation:** track upstream support and add
`user:` once verified.

### 6 — `minio/minio:latest` (open)

`plane-minio` uses the floating `latest` tag.  Pin a dated MinIO release tag
(e.g. `RELEASE.2026-XX-XX…`) for reproducible, auditable deployments.

### 7 — `TRUSTED_PROXIES=0.0.0.0/0` (acceptable)

The Caddy container trusts all proxy ranges by default.  This is safe in this
topology because `plane-proxy` binds **only** to `127.0.0.1` — it is
unreachable from the network.  For defence in depth, set
`TRUSTED_PROXIES=127.0.0.1/32`.

### 8 — `read_only: true` (open)

Marking containers read-only would harden the stack, but the Plane frontend
images run nginx internally and need to chown/write temp directories, and the
backend writes logs.  Applying `read_only` without the corresponding writable
`tmpfs`/volume mounts breaks those images.  **Recommendation:** introduce
`tmpfs: /tmp` plus `read_only: true` service-by-service, validating each with
the test suite.

---

## Already in place (verified)

- `security_opt: [no-new-privileges:true]` on every service.
- `cap_drop: [ALL]` on every service (with the minimal `cap_add` set only
  where required: `CHOWN`/`SETUID`/`SETGID` for the frontends and proxy,
  `NET_BIND_SERVICE` for the proxy).
- Only `plane-proxy` publishes a host port, and only on loopback.
- No hard-coded secrets in the Compose file; all values come from `.env`.
- `.env` is gitignored; `setup.sh install` generates strong hex secrets and
  replaces every `CHANGE_ME_*` placeholder.

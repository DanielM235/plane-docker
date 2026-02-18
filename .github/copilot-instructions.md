# GitHub Copilot Instructions — plane-docker

## Project purpose

This repository defines and tests a **robust production Docker Compose
configuration** for the **Plane Community Edition** (CE) self-hosted project
management application (<https://github.com/makeplane/plane>).

The production target is a **Debian 12** server where **nginx is already
installed at the OS level** and acts as the external reverse proxy.

Reference docs:
- Architecture: <https://developers.plane.so/self-hosting/plane-architecture>
- Docker Compose: <https://developers.plane.so/self-hosting/methods/docker-compose>
- Environment variables: <https://developers.plane.so/self-hosting/govern/environment-variables>
- External reverse proxy: <https://developers.plane.so/self-hosting/govern/reverse-proxy>

---

## Language and documentation

1. **Everything in English** — code comments, README files, test descriptions,
   commit messages, and helper scripts.

---

## Change tracking

2. Update `CHANGES.md` at the root whenever files are added, modified, or
   removed during a development session. The file is used to generate commit
   messages when merging feature branches.

---

## Docker Compose rules

3. Use **Docker Compose v2** syntax. Never add a top-level `version:` key.
4. Follow **Docker Compose best practices**, including security hardening:
   - Drop unnecessary Linux capabilities (`cap_drop: [ALL]`).
   - Mark secrets-bearing containers as `read_only: true` where possible.
   - Use `security_opt: [no-new-privileges:true]` on all containers.
   - Never map internal service ports directly to the host in production; only
     the Plane internal proxy exposes a host port.
5. **Dynamic naming** — all containers, networks, and volumes use
   `${COMPOSE_PROJECT_NAME}` as a prefix so multiple instances can co-exist
   on the same host without collision.  Set `COMPOSE_PROJECT_NAME` in `.env`.

---

## Environment variables

5. Stay **as generic as possible**: every tuneable value lives in an
   environment variable.
6. Maintain an **explicit, comprehensive `.env.example`** file.  Every variable
   must have a comment explaining its purpose and a safe default value.
   Copy `.env.example` to `.env` and fill in secrets before first run.

---

## Plane Docker images

- Registry: Docker Hub (`makeplane/*`) or `artifacts.plane.so/makeplane`
- Image tag: controlled by `APP_RELEASE` (default: `stable`).
- Use the **latest stable CE release** (tag `stable`).
- Services: `web`, `space`, `admin`, `api`, `worker`, `beat-worker`,
  `migrator` (init), `live`, `plane-proxy` (internal Caddy router),
  `postgres`, `redis`, `rabbitmq`, `minio`.

---

## Nginx reverse proxy

8. The repository ships an **nginx configuration** suitable for Debian 12's
   system nginx (`/etc/nginx/sites-available/plane`).
   - It proxies all traffic to the internal `plane-proxy` Caddy container.
   - WebSocket support (`Upgrade`/`Connection` headers) is always included.
   - A template for the nginx server block lives in `nginx/templates/`.
9. The **test environment** must include an nginx container that mirrors the
   production nginx setup. All integration tests hit the nginx layer — never
   the internal services directly.

---

## Testing philosophy

6. Tests live under `tests/` and run **entirely inside Docker**.  Never execute
   tests or application code directly on the host machine.
8. Test runner: **pytest** (Python) inside a dedicated `test-runner` container
   defined in `docker-compose.test.yml`.
9. Tests must cover, at minimum:
   - Instance loads in the browser (HTTP 200 at `/`).
   - After first run the `/god-mode/` route is always accessible and returns
     the expected redirect or setup page.
   - API health endpoint responds correctly.
   - MinIO upload endpoint is reachable.
   - WebSocket upgrade succeeds on the live endpoint.
10. Run tests with:
    ```
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test-runner
    ```

---

## setup.sh

7. `setup.sh` is the **user-facing entry point** to bootstrap the stack.
   It derives from the spirit of the official Plane setup script
   (`curl -fsSL https://github.com/makeplane/plane/releases/latest/download/setup.sh`).
   It handles:
   - Checking prerequisites (Docker, Docker Compose v2).
   - Generating secrets if `.env` does not yet exist.
   - Pulling images.
   - Running migrations.
   - Starting or restarting the stack.

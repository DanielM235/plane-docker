# Secret rotation — safe, stable procedure

This guide explains how to rotate each secret used by the stack with minimal
downtime and a reliable rollback path.  **Always take a backup first**:

```bash
./setup.sh backup
```

Keep a copy of the current `.env` so you can roll back instantly:

```bash
cp .env .env.pre-rotation
```

---

## Golden rules

1. **Backup first** — database + files + `.env`.
2. **Change one secret at a time** — never rotate several secrets in the same
   operation; if something breaks you want a single variable to blame.
3. **Restart only the affected services** — most secrets only require
   recreating the containers that consume them.
4. **Verify after each rotation** — `./setup.sh status`, then
   `./setup.sh test`.
5. **Rollback** — restore `.env.pre-rotation` and recreate the services.

---

## `SECRET_KEY` (Django)

Used to sign sessions, CSRF tokens and password-reset links.

**Impact:** rotating it invalidates **all existing user sessions** — every
user must sign in again.  Password-reset links already sent become invalid.

**Procedure**

```bash
./setup.sh backup
cp .env .env.pre-rotation

# 1. Generate a new key and update .env
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(50))")
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${NEW_KEY}|" .env

# 2. Recreate the backend services that read it
docker compose up -d --force-recreate api worker beat-worker

# 3. Verify
./setup.sh status
```

> Schedule this during a low-traffic window: users will be logged out.

---

## `LIVE_SERVER_SECRET_KEY` (live + backend)

Used to authenticate the realtime (`live`) WebSocket service.  It **must be
identical** across `api`, `worker`, `beat-worker`, `migrator` and `live`.

**Impact:** a brief realtime disconnect while the `live` container restarts.
No login impact.

**Procedure**

```bash
./setup.sh backup
cp .env .env.pre-rotation

# 1. Generate one new key and write it ONCE in .env
NEW_LIVE_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s|^LIVE_SERVER_SECRET_KEY=.*|LIVE_SERVER_SECRET_KEY=${NEW_LIVE_KEY}|" .env

# 2. Recreate EVERY service that reads it, in one command, so they all
#    pick up the same value atomically.
docker compose up -d --force-recreate api worker beat-worker migrator live

# 3. Verify
./setup.sh status
```

> Never change this key on only some of the services — a mismatched key
> silently breaks realtime collaboration.

---

## `POSTGRES_PASSWORD` (database)

Used by `plane-db` and the backend connection string.

**Impact:** brief database disconnect while the backend restarts.  The
database itself does not restart (the password lives in the volume).

**Procedure**

```bash
./setup.sh backup
cp .env .env.pre-rotation

# 1. Change the password inside PostgreSQL first
NEW_PG_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
docker compose exec -T plane-db psql -U plane -d plane \
  -c "ALTER ROLE plane WITH PASSWORD '${NEW_PG_PASS}';"

# 2. Update .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${NEW_PG_PASS}|" .env

# 3. Recreate the backend services that connect to the database
docker compose up -d --force-recreate api worker beat-worker migrator

# 4. Verify
./setup.sh status
```

---

## `RABBITMQ_DEFAULT_PASS` (message broker)

RabbitMQ hashes this password **at first boot** and stores it in the
`mqdata` volume.  Editing `.env` alone has **no effect** on a running
instance — you must rotate it via `rabbitmqctl`.

**Procedure**

```bash
./setup.sh backup
cp .env .env.pre-rotation

# 1. Change the password inside RabbitMQ
NEW_MQ_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
docker compose exec -T plane-mq rabbitmqctl change_password plane "${NEW_MQ_PASS}"

# 2. Update .env
sed -i "s|^RABBITMQ_DEFAULT_PASS=.*|RABBITMQ_DEFAULT_PASS=${NEW_MQ_PASS}|" .env

# 3. Recreate the consumers/producers
docker compose up -d --force-recreate api worker beat-worker migrator

# 4. Verify
./setup.sh status
```

---

## MinIO credentials (`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `AWS_*`)

MinIO stores its root credentials at **first boot** in the `miniodata`
volume; editing `.env` after that has no effect on MinIO itself.  The Django
backend reads the same credentials through the `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` variables.

**Recommended approach — rotate the *access key* used by the backend** rather
than the root credentials:

```bash
# 1. Start a shell in the MinIO container
docker compose exec -it plane-minio sh

# 2. Using the MinIO client, create a new service account / access key.
#    (The exact mc command depends on the MinIO image; for recent releases:)
mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc admin user svcacct add local "$MINIO_ROOT_USER" \
  --access-key "$NEW_ACCESS_KEY" \
  --secret-key "$NEW_SECRET_KEY"
```

Then update `.env`:

```dotenv
AWS_ACCESS_KEY_ID=<NEW_ACCESS_KEY>
AWS_SECRET_ACCESS_KEY=<NEW_SECRET_KEY>
```

…and recreate the backend:

```bash
docker compose up -d --force-recreate api worker beat-worker migrator
./setup.sh status
```

> If you absolutely must change `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`,
> you must re-initialise the MinIO volume (destroys metadata credentials —
> **not** the objects).  Prefer rotating the access key instead.

---

## Checklist after any rotation

```bash
./setup.sh status          # all services healthy?
./setup.sh test            # integration suite green?
# Sign in through the browser and check realtime collaboration works.
```

If anything fails, roll back:

```bash
cp .env.pre-rotation .env
docker compose up -d --force-recreate
./setup.sh status
```

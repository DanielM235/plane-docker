# Migration guide — stepwise Plane CE upgrades

This document describes how to migrate a deployed Plane Community Edition
instance from one release to the next, **step by step**, with a backup before
every step.  The procedure is the same whether you run it locally (to validate
the upgrade before touching production) or on your production Debian 12 host.

> **Always rehearse locally first.**  The whole point of this workflow is to
> reproduce, on your own machine, the exact migration you will later run on
> the deployed instance.

---

## 1. Supported migration path

Plane CE releases are **not always safe to jump over**: each release ships
database migrations and occasionally changes environment variables or image
internals.  This repository therefore pins an explicit, tested upgrade path:

| Step | From | To | Notes |
|------|------|----|-------|
| — | — | `v1.2.3` | Current baseline (this config's `.env.example` default) |
| 1 | `v1.2.3` | `v1.3.0` | Visual redesign; security hardening (see notes) |
| 2 | `v1.3.0` | `v1.3.1` | Security patch release (see notes) |
| 3 | `v1.3.1` | `v1.4.0` | Django 4.2 → 5.2; large security batch |
| 4 | `v1.4.0` | `v1.4.1` | Latest stable; security patch release |

The canonical order is stored in [`releases/order.txt`](../releases/order.txt)
and is consumed automatically by `./setup.sh upgrade`.  Each step has a
matching configuration override in [`releases/`](../releases/) and detailed
release notes in [`docs/releases/`](releases/).

---

## 2. How `upgrade` works

`./setup.sh upgrade` performs the following **for every release step** between
the current `APP_RELEASE` and the target:

1. **Backup** — dump PostgreSQL and archive the MinIO file volume
   (`./setup.sh backup` internally).  The migration **aborts** if the backup
   fails, so a broken step never leaves you without a rollback point.
2. **Apply the per-release override** — update `.env` from
   `releases/<version>.env` (this sets `APP_RELEASE` and pull policy).
3. **Pull** the new images.
4. **Run database migrations** (`docker compose run --rm migrator`).
5. **Restart** the stack (`docker compose up -d --remove-orphans`).
6. **Run the integration test suite** (unless `--skip-tests` is passed).

```
./setup.sh upgrade                 # current version → latest in order.txt
./setup.sh upgrade --to v1.3.1     # stop after a specific release
./setup.sh upgrade --skip-tests    # skip the test suite (faster, less safe)
```

---

## 3. Rehearse the migration locally

On your workstation, with Docker and Docker Compose v2 installed:

```bash
# 1. Start from a fresh copy of the repo, pinned to the baseline.
git clone <your-repo-url> plane-docker && cd plane-docker
./setup.sh install --project-name plane-migtest --domain plane.local --port 8091

# 2. Set the baseline explicitly (the default is already v1.2.3).
#    Check: grep APP_RELEASE .env

# 3. (Optional) seed a minimal amount of real data through the UI so the
#    backup/restore and migration steps are exercised with content.

# 4. Run the stepwise migration with tests after each step.
./setup.sh upgrade
```

If every step passes, the local instance is now on the latest release and the
procedure is proven end-to-end.  Only then run it against production.

---

## 4. Run the migration on the deployed instance

The deployed instance uses the **system nginx** and a real domain, but the
migration steps are identical.  On the Debian 12 host:

```bash
cd /path/to/plane-docker

# 0. Safety net — create a manual backup first and keep a copy off-server.
./setup.sh backup
cp -r backups/<latest-timestamp> /somewhere/off-server/

# 1. Put the instance in a known state.
./setup.sh status          # all services healthy?

# 2. Migrate.  Tests are skipped by default on production for speed, but the
#    backup-before-every-step protection is always active.
./setup.sh upgrade --skip-tests

# 3. Verify.
./setup.sh status
curl -sI https://plane.example.com/ | head -5
```

> **Recommendation for production:** run `./setup.sh upgrade` **without**
> `--skip-tests` for maximum safety if the instance can tolerate the extra
> time.  The test suite only reads public routes and does not modify data.

---

## 5. Rolling back

Every step is preceded by a full backup, so rollback is:

```bash
# 1. Stop the stack.
./setup.sh stop

# 2. Pin back to the previous release.
sed -i 's|^APP_RELEASE=.*|APP_RELEASE=<previous-version>|' .env

# 3. Restore the database and files from the backup taken before the step.
./setup.sh restore backups/<timestamp-of-that-step>

# 4. Start again (migrations will run against the restored database).
./setup.sh start
```

See [docs/BACKUP_RESTORE.md](BACKUP_RESTORE.md) for the exact layout of a
backup and what is (and is not) included.

---

## 6. Per-release notes

Each migration step has its own document, focused on what an operator must
know **before** applying that step:

- [`v1.3.0`](releases/v1.3.0.md)
- [`v1.3.1`](releases/v1.3.1.md)
- [`v1.4.0`](releases/v1.4.0.md)
- [`v1.4.1`](releases/v1.4.1.md)

---

## 7. Adding a future release (v2.x, v3.x, …)

When a new release is published:

1. Create `releases/<version>.env` with `APP_RELEASE=<version>`.
2. Append `<version>` to [`releases/order.txt`](../releases/order.txt).
3. Add release notes at `docs/releases/<version>.md`.
4. Run `./setup.sh test` and, ideally, `./setup.sh upgrade --to <version>`.

> The versioning scheme may change upstream (e.g. a future `v2.0.0`).  The
> workflow is version-agnostic: only the entries in `releases/order.txt` and
> the corresponding `.env` / `.md` files matter.

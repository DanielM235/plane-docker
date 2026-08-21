# Changelog

This file tracks every change to the plane-docker configuration wrapper.
It is used to generate commit messages when merging feature branches.

## 1.1.0 — stepwise Plane migration, backup/restore (2026-08-20)

### Added
- `releases/` directory with per-release configuration overrides
  (`v1.2.3`, `v1.3.0`, `v1.3.1`, `v1.4.0`, `v1.4.1`) and the canonical
  migration order in `releases/order.txt`.
- `./setup.sh backup` — dumps PostgreSQL and archives the MinIO file volume
  into a timestamped directory under `BACKUP_DIR`.
- `./setup.sh restore <backup-dir>` — restores a backup (database + files).
- `./setup.sh upgrade [--to VERSION] [--skip-tests]` — stepwise migration
  through the release order, with a backup before every step and the
  integration test suite run after every step by default.
- New `.env` variables: `BACKUP_DIR`, `BACKUP_RETENTION_DAYS`.
- Documentation: `docs/MIGRATION.md`, `docs/BACKUP_RESTORE.md`, and per-step
  release notes under `docs/releases/`.
- Tests: `tests/test_07_backup.py`, `tests/test_08_upgrade.py`,
  `tests/test_09_releases.py`, plus a shared `tests/support.py` helper.

### Fixed (production hardening)
- `live` service: added the required `LIVE_SERVER_SECRET_KEY` (which caused a
  100%-CPU crash-loop when missing) and `REDIS_URL`/`REDIS_HOST`/`REDIS_PORT`
  (live could not find Redis), plus a `plane-redis` health dependency.
- Backend services (`api`, `worker`, `beat-worker`, `migrator`) now share the
  same `LIVE_SERVER_SECRET_KEY` as `live`.
- Added healthchecks to `worker`, `beat-worker` and `live`.
- Added CPU / memory / pids resource limits to every service (tunable via
  `*_CPU_LIMIT` / `*_MEM_LIMIT` environment variables).
- `setup.sh install` now generates `LIVE_SERVER_SECRET_KEY`.
- `plane-db` now runs as `user: postgres` (instead of relying on `cap_add`)
  so `cap_drop: [ALL]` no longer blocks PostgreSQL's first-boot
  `chown`/`chmod`/`su-exec` privilege switch ("chmod … Operation not
  permitted" fix).  `plane-redis` uses the same pattern (`user: redis`).
- Backend services (`api`, `worker`, `beat-worker`, `migrator`) now run as
  `user: nobody` — silences Celery's "superuser privileges" warning and
  removes root from every long-running service.
- `setup.sh install` now warns when `vm.overcommit_memory` is not `1` on the
  host (required by Redis for reliable background saves); documented in
  `docs/TROUBLESHOOTING.md`.
- Added `tests/test_11_db_first_boot.py` — boots `postgres:15.7-alpine` as
  `user: postgres` with `cap_drop: [ALL]` against a **fresh** volume and
  asserts a successful `initdb`/first start (the Docker socket is mounted
  into the test-runner for this; the test skips if it is unavailable).

### Added (hardening docs)
- `docs/SECRET_ROTATION.md` and `docs/SECURITY_AUDIT.md`.
- `tests/test_10_compose_audit.py` (static Compose audit) and a
  `LIVE_SERVER_SECRET_KEY` generation test in `test_06_setup_sh.py`.

### Changed
- `VERSION` bumped to `1.1.0`.
- `.env.example` baseline `APP_RELEASE` aligned to `v1.2.3`.
- `README.md` and `docs/ENV_VARS.md` updated for the new commands, variables,
  and hardening.

## 1.0.0 — initial production stack

- Initial production Docker Compose configuration for Plane CE v1.2.1.
- `setup.sh` with `install`, `start`, `stop`, `restart`, `pull`, `migrate`,
  `status`, `test`, `logs`.
- nginx templates, pytest integration suite (26 tests), and core docs.

# Backup & restore

Plane persists state in two places that must both be backed up together:

| Data | Where it lives | Backed up as |
|------|----------------|--------------|
| Relational data (workspaces, issues, users, …) | PostgreSQL (`plane-db`) | `pg_dump` custom-format dump |
| Uploaded files (attachments, assets, …) | MinIO volume (`<project>_miniodata`) | gzip-compressed `tar` archive |

Redis (cache) and RabbitMQ (task queues) are intentionally **not** backed up —
their content is ephemeral and rebuilt automatically.

---

## 1. Create a backup

```bash
./setup.sh backup
```

The backup is written to `BACKUP_DIR/<timestamp>/` (default: `backups/`):

```
backups/
└── 20260820-143000/
    ├── manifest.txt          # timestamp, project, APP_RELEASE, volumes
    ├── .env                  # copy of the environment (contains secrets — keep safe)
    ├── db/
    │   └── plane.dump        # PostgreSQL custom-format dump (pg_dump -Fc)
    └── files/
        └── minio-data.tgz    # MinIO volume archive (tar.gz)
```

The PostgreSQL dump uses `--format=custom --no-owner --no-privileges`, so it
restores cleanly into a fresh database of a newer release (custom format is
version-tolerant).

The MinIO archive is produced by streaming the volume through `tar`, so no
temporary copy of the volume is written to disk first.

Retention: backups older than `BACKUP_RETENTION_DAYS` (default `30`) are
pruned automatically by `./setup.sh backup`.

---

## 2. Restore a backup

```bash
./setup.sh restore backups/20260820-143000
```

Restore performs:

1. Ensure `plane-db` is running.
2. Restore the database with `pg_restore --clean --if-exists`.
3. Restore the MinIO volume from the archive.

> **When to restore:** restore onto a **stopped** stack whenever possible.
> If services are running, stop them first to avoid application state racing
> with the database restore:
>
> ```bash
> ./setup.sh stop
> ./setup.sh restore backups/20260820-143000
> ./setup.sh start
> ```

---

## 3. Backup before every migration step

`./setup.sh upgrade` calls the backup routine automatically **before every
release step**.  You therefore always have a rollback point matching the exact
version that was running before the step.

For production, also copy the latest backup off-server:

```bash
./setup.sh backup
rsync -av backups/ user@backup-host:/plane-backups/
```

---

## 4. Testing backups

Backups are tested two ways in this repository:

1. **Unit tests** (`tests/test_07_backup.py`) verify that `backup`/`restore`
   invoke the right tools (`pg_dump`/`pg_restore` against `plane-db`, `tar`
   against the MinIO volume) and produce the expected directory layout.
2. **Real-Docker validation** — after bringing the stack up, run
   `./setup.sh backup` and confirm `plane.dump` and `minio-data.tgz` are
   non-trivial.

# Versioned release configuration

This directory holds one small configuration override per Plane CE release
step in the supported migration path.

## Files

| File | Purpose |
|------|---------|
| `order.txt` | Canonical migration order (oldest → newest). Consumed by `./setup.sh upgrade`. |
| `vX.Y.Z.env` | Per-version override applied to `.env` when that release is reached. |

Each `vX.Y.Z.env` pins `APP_RELEASE` to the matching Plane image tag and
sets a safe `PULL_POLICY`.  `./setup.sh upgrade` applies these files one at
a time, in the order given by `order.txt`, from the version currently set in
`.env` up to the requested target.

## Adding a new release

1. Add a new `vX.Y.Z.env` file with `APP_RELEASE=vX.Y.Z`.
2. Append `vX.Y.Z` to `order.txt`.
3. Add release notes under `docs/releases/vX.Y.Z.md`.
4. Run `./setup.sh test` to revalidate.

See `docs/MIGRATION.md` for the full migration procedure.

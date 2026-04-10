# openoutreach

CLI for [OpenOutreach](https://openoutreach.app) Premium — provision and manage your cloud LinkedIn automation instance.

## Install

```bash
pip install openoutreach
```

## Commands

```
openoutreach signup                                # Sign up via Stripe checkout
openoutreach up <data-dir>                         # Provision cloud instance, upload db.sqlite3
openoutreach status                                # Check instance status
openoutreach logs                                  # Stream live logs from your instance (mTLS)
openoutreach down [--backup-path PATH]             # Download DB, then destroy the instance
```

## Data lifecycle

OpenOutreach Premium runs a full **local → cloud → local** loop around your SQLite DB:

1. `openoutreach up ./data` provisions a droplet and uploads `./data/db.sqlite3` — the DB carries your LinkedIn session, campaigns, and accumulated leads.
2. The cloud instance runs the daemon, enriching leads and updating campaign state. Everything is written back to the same SQLite file on the droplet.
3. `openoutreach down` pulls the updated `db.sqlite3` back to your machine **before** destroying the droplet. You keep every row the daemon wrote while the instance was running.

Key points about `down`:

- The download happens first; the droplet is only destroyed after the backup file is on local disk. If the download fails, the droplet is left running and you can re-run the command.
- Default backup path is `./db.sqlite3` in the current directory. If that file already exists you'll be prompted before overwriting. Pass `--backup-path /path/to/file.sqlite3` to save elsewhere.
- `--no-download` skips the download step entirely and just destroys the droplet — use only if you already have a copy or don't care about the accumulated state.
- The command is safe to retry: successful teardown is idempotent.

# Deploying Brick Tidy on a Synology NAS

The NAS runs the image that CI publishes to the GitHub Container Registry,
`ghcr.io/frankandreas/brick-tidy:latest`, whenever a change lands on `main` and the tests pass.
The repository is public, so no registry login is needed.

Everything worth keeping lives in one folder, `data/`: the SQLite database (catalog, projects,
detections), the photos and the downloaded Rebrickable CSVs.

## Prerequisites
- A Synology NAS with **Container Manager** installed.
- SSH access to the NAS (**Control Panel → Terminal & SNMP → Enable SSH**).

## Installation

1. **Create a user for the app** (recommended). In DSM, **Control Panel → User & Group → Create** a
   user such as `bricktidy`, with a random password and no application permissions. On the shared
   folder that holds the app folder (e.g. `docker`), give it **Read only** (or no access): the nightly
   update runs as root, so the app's user must not be able to change `compose.yaml` or
   `nas-update.sh`. It still writes `data/`, because it owns it. Look up its IDs over SSH:
   ```bash
   id bricktidy    # e.g. uid=1032(bricktidy) gid=100(users)
   ```
   Skip this step to run as the image's built-in user instead (1000:1000).
2. **Prepare the folder.** Create the app folder (e.g. `/volume1/docker/brick-tidy`), upload
   `docker-compose.nas.yml` into it **renamed to `compose.yaml`**, and change the port in it if 8080
   is taken. Then create the data folder, owned by the app's user:
   ```bash
   cd /volume1/docker/brick-tidy
   sudo mkdir -p data
   sudo chown -R 1032:100 data    # the IDs from step 1, or 1000:1000
   ```
3. **Create `.env`** with the user's IDs and, optionally, the API keys (see `.env.example`):
   ```bash
   printf 'PUID=1032\nPGID=100\n' | sudo tee .env    # IDs from step 1
   sudo chmod 600 .env
   ```
   Add `ANTHROPIC_API_KEY=...` (Claude fallback for set and brick recognition) and
   `REBRICKABLE_API_KEY=...` (better part-ID mapping) if you have them. Keep `.env` private.
4. **Import the Rebrickable catalog** (about a minute on a NAS; the database grows to ~160 MB):
   ```bash
   sudo docker-compose run --rm brick-tidy python /app/scripts/import_rebrickable.py --download
   ```
5. **Start** with `sudo docker-compose up -d`, or create a Container Manager **Project** on this folder
   ("Create project with existing docker-compose.yml").
6. Open `http://<NAS_IP>:8080` on the phone.

Re-run step 4 now and then to pick up new sets; it replaces the catalog and keeps your projects.

## Updates
Set up [automatic updates](#automatic-updates) to install new versions nightly, or update by hand:

```bash
sudo docker-compose pull
sudo docker-compose up -d
```

## Automatic updates

`scripts/nas-update.sh` checks for a new image and installs it. Run nightly by DSM's Task Scheduler, it:

1. pulls the image; if nothing is new, it only checks that the app is healthy and stops,
2. backs up the database to `db-backups/` with SQLite's online-backup API (keeps at least the newest
   5 and everything from the last 7 days; the app migrates the database when it starts),
3. keeps the running image tagged as `:previous` for [rolling back](#rolling-back-an-update),
4. restarts the app and waits until it is healthy, logging to `logs/update.log`.

It finds the compose file by its usual names (`compose.yaml`, `docker-compose.yml`, ...) and never
changes it or `.env`. It also removes dangling (untagged) Docker images to save space, including
those of other projects on the NAS. Photos are not touched by updates and are not in these backups;
include `data/` in your regular NAS backup (e.g. Hyper Backup).

### Setup
1. Copy `scripts/nas-update.sh` into the app folder (next to the compose file) and make it executable:
   ```bash
   sudo chmod +x /volume1/docker/brick-tidy/nas-update.sh
   ```
2. Run it once by hand and check that it ends with "Up to date" or "Update complete":
   ```bash
   sudo /volume1/docker/brick-tidy/nas-update.sh
   ```
3. In DSM, open **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**:
   - **General**: any name (e.g. `Brick Tidy update`), user **root**
   - **Schedule**: daily, at a quiet time (e.g. 03:45)
   - **Task Settings**: tick **Send run details by email** and **only when the script terminates
     abnormally**, and enter the command:
     ```bash
     bash /volume1/docker/brick-tidy/nas-update.sh
     ```

### Rolling back an update
If a new version misbehaves, go back to the previous one. First **disable the scheduled task**,
otherwise the next night installs the same version again. Then, in the app folder:

```bash
cd /volume1/docker/brick-tidy
# 1. Point latest at the previous image
sudo docker tag ghcr.io/frankandreas/brick-tidy:previous ghcr.io/frankandreas/brick-tidy:latest
# 2. Restore the database from the backup taken right before the update
#    (the new version may have migrated it). Changes made since the update are lost.
sudo docker stop brick-tidy
sudo sh -c 'gunzip -c db-backups/brick-tidy-YYYYMMDD-HHMMSS.sqlite.gz > data/brick_tidy.sqlite3'
# 3. Start the previous version
sudo docker-compose up -d
```

Re-enable the task once a fixed version is published.

### Deploying from your computer
To install the latest version right away, or to roll out a new version of `nas-update.sh`, run from
a checkout of this repository:

```bash
cp .deploy.env.example .deploy.env   # once; then set NAS_HOST and NAS_DIR in it
scripts/deploy-nas.sh
```

It shows how the NAS's `nas-update.sh` differs from the repository's, uploads it after you confirm,
and runs the update. Add `--force` to restart the app even without a new version, e.g. after editing
`.env` or the compose file on the NAS. The compose file and `.env` belong to your deployment and are
never copied.

- Connect once by hand (`ssh user@<NAS_IP>`) to verify and accept the NAS's host key; the script
  never accepts unknown keys.
- **Password:** without further setup, ssh and sudo ask for it. To avoid typing it, set
  `NAS_PASS_CMD` in `.deploy.env` to a command that prints it, e.g. `NAS_PASS_CMD=pass show nas`. The
  password is piped straight to ssh and `sudo` and never stored, shown or logged.
- `.deploy.env` is gitignored, so your NAS address stays out of the repository.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `PermissionError` / *unable to open database file* in `sudo docker-compose logs` | `data/` doesn't belong to `PUID:PGID` | `sudo chown -R <PUID>:<PGID> data` |
| The app says the catalog is missing | The import (step 4) hasn't run, or ran with another compose file | Run step 4 in the app folder |
| Email from Task Scheduler: *up to date, but the app is not healthy* | The app keeps crashing without an update, e.g. after a change to `.env` | Check `logs/update.log`, fix the cause, then `sudo docker-compose up -d` |
| Email from Task Scheduler: *app not healthy* | A new version fails to start | Check `logs/update.log` (it ends with the app's log), then [roll back](#rolling-back-an-update) |
| Uploads are slow | Brick recognition is network-bound (Brickognize), not CPU-bound | Usually fine on a NAS; detection itself takes ~0.5 s per photo |

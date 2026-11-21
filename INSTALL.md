# Photo Audit — Installation

These steps use a **QNAP NAS** with Container Station as the example. Any NAS or server that runs Docker
Compose and can see the media folder works the same way; only the paths differ (see the Synology notes
in `.env.example`).

Throughout, replace the placeholders with your own values:

| Placeholder | Meaning | Example |
|---|---|---|
| `<nas>` | The NAS's network name or IP | `mynas` / `192.168.1.50` |
| `<media-share>` | The shared folder holding everyone's photos and videos | `Multimedia` |
| `<app-dir>` | Where the app's files live on the NAS | `/share/Container/photo-audit/app` |

**Where everything runs**

| Machine | What runs there | For how long |
|---|---|---|
| **NAS** | The whole app: database, web server, background worker (Docker) | Permanently |
| **Helper PC** (optional) | One extra *worker* container that speeds up the first big indexing run | Temporarily, then uninstall |
| Everyone's PC / Mac | Just a web browser: `http://<nas>:8090` | — |

Steps run in this order: **A (NAS)**, then optionally **B (helper PC)**, then **C (finish)**. For a small library (under ~20k files), skip B; the NAS indexes everything by itself, just more slowly.

---

## A. NAS setup (once)

1. **Install Container Station.** In QTS, open **App Center**, search for *Container Station*, and install it.
2. **Enable SSH.** Go to **Control Panel → Network & File Services → Telnet / SSH**, tick *Allow SSH connection* (port 22), and click Apply.
3. **Create folders.** In **Control Panel → Shared Folders**, create a shared folder named `Container` if it doesn't exist yet. Then use File Station or Windows Explorer to create these two folders:
   - `\\<nas>\Container\photo-audit\app`
   - `\\<nas>\Container\photo-audit\thumbs`
4. **Organise the media share.** The app expects **one top-level folder per person** inside `<media-share>` (e.g. `\\<nas>\<media-share>\Alex`, `\\<nas>\<media-share>\Sam`). Whoever owns a folder is the person who approves deleting files from it. Files elsewhere in the share are "unassigned", and anyone can approve them.
5. **(Only for the helper PC) Create a service account.** Go to **Control Panel → Users → Create**. Name it e.g. `photoaudit` and give it a password using **only letters and numbers** (it is passed as an SMB option, so commas and quotes break it). Give it **Read/Write** on `<media-share>` and `Container`.
6. **Copy the app.** Put the project files (`Dockerfile`, `docker-compose.yml`, `backend/`, `frontend/`, …) into `\\<nas>\Container\photo-audit\app`. You can download the repository as a ZIP from GitHub and extract it there, or `git clone` it if your NAS has git. Leave out `frontend/node_modules` and `data/` if they exist.
7. **Configure.** In that folder, copy `.env.example` to `.env` and edit it in Notepad. The required values:
   ```ini
   DB_PASSWORD=<long random string>        # e.g. 40 random letters/digits
   SECRET_KEY=<another long random string>
   LIBRARY_PATH=/share/<media-share>
   THUMBS_PATH=/share/Container/photo-audit/thumbs
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=                         # empty = random password printed in the log
   ```
   Everything else in `.env.example` is optional: the app name, port, map tiles, timezone and resource limits. See **Configuration** in `README.md`.
   > If Windows saves it as `.env.txt`, rename it. Turn on *View → File name extensions* in Explorer to see the real name.
8. **Build and start.** Open PowerShell (or Terminal on a Mac):
   ```powershell
   ssh <your-nas-admin-user>@<nas>
   ```
   Then, on the NAS:
   ```sh
   cd <app-dir>
   docker compose up -d --build
   ```
   The first build downloads and compiles everything and takes about 5–15 minutes. Check that it worked:
   ```sh
   docker compose ps          # db, api and worker should show "running" / "healthy"
   docker compose logs api | grep -A6 "admin account"
   ```
   The last command prints the **admin username and password**. Note them down.
   > If `docker` isn't found, sign out of SSH and back in after Container Station finishes installing. On some QTS versions the binary is at `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker`.
9. **Sign in as admin and create the users.** Open **http://<nas>:8090** and sign in as the admin.
   - Under **Settings → Users → Add a user**, add each person. Their *Folder* is the name of their top-level folder in `<media-share>`, which makes them the owner of the files in it. Tick *Admin* for anyone else who should be able to manage users.
   - Change the admin password under **Settings → My account**.
   - Tell everyone to open the site, type their name and **leave the password empty**. They'll be asked to choose their own password.
   > The admin is a normal user too (it can review, approve and so on). Its only extra power is managing users. Give the admin account a folder only if it should own files.
10. **Using the helper PC?** Pause scheduled scans so the NAS doesn't start the big first scan by itself: in **Settings → Scanning**, tick *Pause scheduled scans* and click **Save settings**. Without a helper PC, skip this step; the first scan starts within a minute.

> **The quarantine folder.** When an owner approves a deletion, the file moves to `\\<nas>\<media-share>\.quarantine\<date>\…` (the name is set by `QUARANTINE_DIRNAME`). Windows shows this folder; please don't edit it by hand. Use the app's **Quarantine** page to restore or purge files.

### Alternative to steps A6–A8: deploy from another computer over SSH

If you'd rather not copy files by hand or work in an SSH shell, any computer with **git** and **OpenSSH** (both built into Windows 11) can push the code to the NAS and run it there. You don't need Docker on that computer. Do steps A1–A5 first. (Docker's own remote mode, `docker context`, doesn't work with QNAP: over SSH, Container Station's `docker` isn't on the PATH.)

1. **Key-based SSH**, so commands run without a password prompt. On your computer:
   ```powershell
   ssh-keygen -t ed25519            # skip if ~/.ssh/id_ed25519 already exists; press Enter for defaults
   type $env:USERPROFILE\.ssh\id_ed25519.pub
   ```
   Then, **in a normal PowerShell window** (it has to prompt for your NAS password):
   ```powershell
   ssh <nas-admin>@<nas> "mkdir -p ~/.ssh && echo '<paste the line here>' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && echo KEY-ADDED"
   ssh -o BatchMode=yes <nas-admin>@<nas> echo key-login-ok     # must work with no password prompt
   ```
   > If the password is refused: in **Control Panel → Network & File Services → Telnet / SSH → Edit Access Permission**, make sure the account is allowed to use SSH. If key login stops working after a NAS reboot, enable home folders under **Control Panel → Users → Advanced Settings** and repeat this step.
2. **Configuration file:** create `.env.live` next to `docker-compose.yml` on your computer (copy `.env.example`). The paths in it are **paths on the NAS** (e.g. `/share/Multimedia`). It is gitignored; keep it safe, because it holds the secrets.
3. **Deploy.** From the repository folder (Git Bash or PowerShell). This is also the update procedure after a `git pull`:
   ```sh
   git archive --format=tar HEAD | ssh <nas-admin>@<nas> "tar -x -C /share/Container/photo-audit/app"
   scp .env.live <nas-admin>@<nas>:/share/Container/photo-audit/app/.env
   ssh <nas-admin>@<nas> "chmod 600 /share/Container/photo-audit/app/.env; chmod +x /share/Container/photo-audit/app/qnap-compose.sh"
   ssh <nas-admin>@<nas> "/share/Container/photo-audit/app/qnap-compose.sh up -d --build"
   ```
   The build runs on the NAS (5–15 minutes the first time). Get the admin password with
   `ssh <nas-admin>@<nas> "/share/Container/photo-audit/app/qnap-compose.sh logs api" | findstr password:`
   (`grep password:` in Git Bash), then continue with step A9.

**Managing Live afterwards** uses the same wrapper, which finds Container Station's docker and gives it a writable config folder:
```sh
ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh ps
ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh logs --tail 100 worker
ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh restart
ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh exec api python -m app.cli users
```
In an SSH shell on the NAS, `cd /share/Container/photo-audit/app && ./qnap-compose.sh <command>` does the same.

---

## B. Helper PC setup (optional; Windows 11, assumes nothing is installed)

This PC runs a second worker to help with the first index of a large library. It reads the media over the network and writes thumbnails and results back to the NAS.

1. **Enable WSL2.** Open **PowerShell as Administrator** and run:
   ```powershell
   wsl --install
   ```
   Then reboot.

2. **Install Docker Desktop**:
   ```powershell
   winget install -e --id Docker.DockerDesktop
   ```
   (or download it from https://www.docker.com/products/docker-desktop). Start **Docker Desktop**, accept the terms, keep the default **WSL 2 backend**, and wait until it shows **Engine running**. Skip sign-in.

3. **Copy the project** from `\\<nas>\Container\photo-audit\app` to `C:\photo-audit`. Explorer drag-and-drop works, or download the repository ZIP from GitHub.

4. **Find the NAS's IP address and check that it's reachable**:
   ```powershell
   ping -4 <nas>                                 # note the IP, e.g. 192.168.1.50
   Test-NetConnection 192.168.1.50 -Port 445     # SMB      -> TcpTestSucceeded : True
   Test-NetConnection 192.168.1.50 -Port 5432    # database -> TcpTestSucceeded : True
   ```
   If port 5432 fails, the NAS stack isn't running (go back to step A8), or a firewall on the NAS (QTS **Security Counselor / QuFirewall**) is blocking it. If you changed `DB_PUBLISH_PORT`, test that port instead.

5. **Configure.** In `C:\photo-audit`, copy `.env.pc-worker.example` to `.env.pc-worker` and edit it:
   ```ini
   NAS_IP=192.168.1.50                         # the IP from step 4; use the IP, not the name
   DB_PASSWORD=<same as DB_PASSWORD in the NAS .env>
   SMB_LIBRARY_SHARE=<media-share>             # the share LIBRARY_PATH points to
   SMB_THUMBS_PATH=Container/photo-audit/thumbs
   SMB_USER=photoaudit
   SMB_PASSWORD=<the photoaudit password>
   PC_WORKER_CONCURRENCY=7                     # logical CPU cores minus 1 (Task Manager -> Performance -> CPU)
   ```

6. **Keep the PC awake.** Go to **Settings → System → Power** and set *Screen and sleep* to **Never** while the scan runs.

7. **Start the worker**:
   ```powershell
   cd C:\photo-audit
   docker compose -f docker-compose.pc-worker.yml --env-file .env.pc-worker up --build
   ```
   It builds the image (about 5–10 minutes the first time). Then it logs `worker pc: 7 processes, scheduler off` and waits for work.

8. **Start the scan.** In the web app, open **Settings** and click **Scan now**. The NAS worker and the PC worker share the work automatically. Track progress on the **Overview** page or under **Settings → System status**. Both `nas` and `pc` workers appear there.

---

## C. Finish the initial scan

1. When **System status** shows *0 pending* and the latest `group` job is `done`, go back to the PC window and press **Ctrl+C**. Then run:
   ```powershell
   docker compose -f docker-compose.pc-worker.yml --env-file .env.pc-worker down -v
   ```
   (`-v` only removes the PC's network-share mount definitions. Nothing on the NAS is touched.)
2. In the app, go to **Settings**, untick *Pause scheduled scans*, and **Save**. From now on the NAS picks up new uploads every 30 minutes (configurable).
3. Optional: uninstall Docker Desktop from the PC (**Settings → Apps**).
4. Optional: once you no longer need the helper PC, stop exposing the database on the network. In the NAS `docker-compose.yml`, delete the `ports:` section under `db`, then run `docker compose up -d`.

---

## Day-to-day

- **Reviewing.** Use **Overview → Start reviewing**. Keyboard shortcuts: `1`–`9` select a copy, `K` keep, `D` delete, `Enter` save and go to the next group, `S` skip, `Z` zoom, `?` show help.
- **Viewing.** Click any photo or video (Browse, Map, Approvals, Quarantine), or press `V` / double-click in Review, to open the full-screen viewer: `←` `→` browse, `+` `−` `0` zoom (or mouse wheel / double-click, drag to pan), `F` full screen, `I` info, `L` Live Photo motion, `Esc` close. Nothing is downloaded unless you press **Download**.
- **Approvals.** Every deletion needs the **file owner's** approval (the **Approvals** page shows a red badge). Files outside every user's folder are "unassigned", and anyone can approve them.
- **Quarantine.** Approved files stay restorable for 30 days by default. **Purge eligible** removes them for good. Auto-purge is off unless you enable it in Settings.
- **Forgotten password.** An admin opens **Settings → Users** and clicks **Reset password** next to the person. They're signed out, and the next time they sign in (name only, empty password) they choose a new password.
- **Someone leaves.** Click **Deactivate**. They can't sign in anymore, but their history and file ownership stay intact.
- **Locked-out admin** (nobody knows an admin password). On the NAS, over SSH:
  ```sh
  cd <app-dir> && docker compose exec api python -m app.cli admin-password admin
  ```
  This prints a new password for `admin`. `python -m app.cli users` lists all accounts.

## Updating the app

Copy the new project files over `<app-dir>` and keep your `.env`. Then run:
```sh
cd <app-dir> && docker compose up -d --build
```
Database changes are applied automatically when the app starts.

## Backups

The database holds every decision and the audit log. Back it up occasionally:
```sh
cd <app-dir>
docker compose exec -T db pg_dump -U photoaudit photoaudit | gzip > ../backup-$(date +%F).sql.gz
```
You can regenerate thumbnails at any time, so they don't need a backup.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page won't load at `:8090` | `docker compose ps` on the NAS, then `docker compose logs api` |
| `port is already allocated` on start | Another service uses `APP_PORT` (QNAP's admin page uses 8080). Pick a free port in `.env` |
| Nothing appears after a scan | **Settings → System status**: look at the job list and *Files that failed to process* (**Retry all**) |
| PC worker: `mount error(13): Permission denied` | Wrong `SMB_USER`/`SMB_PASSWORD`, or the account lacks rights on the shares. Fix it, then `down -v` and `up` again |
| PC worker: `mount error(2)` / `No such file or directory` | `SMB_LIBRARY_SHARE` or `SMB_THUMBS_PATH` doesn't match the share/folder names on the NAS |
| PC worker: `mount error(95)` | Older NAS firmware: set `SMB_VERSION=2.1` |
| PC worker: `could not connect to server` | `NAS_IP` or `DB_PORT` is wrong, or the database port is blocked (step B4) |
| A video says "Preparing a playable preview" | iPhone HEVC videos are converted the first time someone watches one. Wait a few seconds |
| Too many / too few "same photo" matches | **Settings → Duplicate detection**: lower or raise *Re-encode threshold*, then **Re-run grouping** |
| No internet on the NAS / map is blank | Set `MAP_TILE_URL` to a local tile server, or `MAP_TILE_URL=off` to hide the map |

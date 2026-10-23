# Mounting the host drive (read-only WebDAV)

The host serves a **read-only WebDAV** view of the catalogue at `http://<host>:8765/dav/`.
macOS Finder and the Windows WebDAV redirector can mount it with **no third-party
component and no licence cost** — which is why we are not shipping our own filesystem
(see `COSTS.md` E1/E2: WinFsp US$6,000/3yr, macFUSE paid bundling).

> **Status: protocol-verified, not mount-verified.** 22 automated tests cover the
> WebDAV behaviour (PROPFIND, ranges, refusals, offline 503, traversal). **No OS client
> or NLE has been driven against it yet.** Treat the commands below as the test script
> for your machines, not as a proven workflow. Record results in `NLE_TEST_LOG.md`.

---

## Before you start

| Item | Value |
|---|---|
| URL | `http://<host-ip>:8765/dav/` |
| Username | anything (ignored) |
| Password | the host bearer token — `data/catalogue.token` on the host, or `LFMC_TOKEN` |
| Protocol | HTTP Basic over **plain HTTP** → LAN or trusted VPN only until TLS lands |
| Access | read-only: `GET, HEAD, OPTIONS, PROPFIND`. Writes are refused with 405 |

Get the token on the host:

```bash
cat data/catalogue.token          # written with mode 0600 when not in --demo mode
```

Do **not** use `--demo` for anything except the synthetic sample drive: it disables
authentication entirely.

---

## macOS (Finder, built in)

1. Finder → **Go ▸ Connect to Server…** (⌘K)
2. Enter `http://<host-ip>:8765/dav/`
3. Choose **Registered User**: name = anything, password = the token
4. The drive appears under `/Volumes/dav` (or the name you gave it)

Command-line equivalent:

```bash
mkdir -p ~/lfmc_mount
mount_webdav http://<host-ip>:8765/dav ~/lfmc_mount
# unmount: umount ~/lfmc_mount
```

**Known macOS caveats to test on your machine**
* `webdavfs` is aggressive about caching and may not honour every `Range` request
  itself; what matters is what the *NLE* asks for, so measure bytes with
  `curl "…/api/transfers"` while scrubbing.
* Finder may present the share as `dav` rather than the root label — paths inside are
  what matter (`/dav/<Root Label>/<folder>/<file>`).

## Windows 10/11 (WebClient / WebDAV redirector, built in)

**Two defaults must be raised or large video will fail.** The redirector ships with a
50 MB file-size limit and Basic auth disabled over HTTP. Both are per-machine settings
the user changes on their own computer (no cost, no third party). In an **elevated**
PowerShell:

```powershell
# 1. Allow Basic auth over HTTP (default 1 = SSL-only, which we do not have yet)
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Services\WebClient\Parameters `
  -Name BasicAuthLevel -Value 2

# 2. Raise the redirector's 50 MB file-size ceiling (0xFFFFFFFF ≈ 4 GB)
Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Services\WebClient\Parameters `
  -Name FileSizeLimitInBytes -Value 4294967295

# 3. Restart the service so the changes apply
Restart-Service WebClient
```

Then map the drive:

```cmd
net use Z: http://<host-ip>:8765/dav/ /user:editor <TOKEN>
```

**Known Windows caveats to test on your machine**
* If `WebClient` is not running, `net use` fails with a vague "network path not found".
* The redirector is widely reported to behave poorly with very large sequential reads;
  this is exactly the Gate A risk. If it stalls, fall back to rclone (below) and record
  the difference in `NLE_TEST_LOG.md`.
* Windows may refuse `http://` shares by policy; group policy can override the registry
  change above on managed machines.

## rclone (user-installed, MIT-licensed, any OS)

The blueprint's $0 rule and the licence analysis both point here for a stronger mount:
**the user installs rclone themselves**, so no driver licence question reaches us.

```bash
rclone config create lfmc webdav \
  url=http://<host-ip>:8765/dav/ vendor=other user=editor pass=$(rclone obscure '<TOKEN>')

# macOS / Linux
rclone mount lfmc: ~/lfmc_mount \
  --vfs-cache-mode full --vfs-cache-max-size 200G \
  --dir-cache-time 30s --read-ahead 16M --async-read=false --daemon \
  --log-file ~/lfmc-rclone.log --log-level INFO
```

`--vfs-cache-mode full` is required: it turns rclone into an on-demand, ranged-read
cache, which is the behaviour an NLE needs. Mounting with `--vfs-cache-mode off` will
make Premiere/Resolve unhappy and is not a supported configuration for video.

**Measure the cache, don't guess:**

```bash
curl "http://<host-ip>:8765/api/transfers"   # bytes actually served, per range
```

Compare with the file size: that ratio *is* the "no full-file staging" evidence for
Gate A. Record it in `NLE_TEST_LOG.md`.

---

## What the mount guarantees, and what it explicitly does not

| Guarantee | How |
|---|---|
| Source media is never modified | All write verbs are refused with 405 before touching the filesystem; `DELETE`/`PUT`/`MKCOL`/`PROPPATCH`/`COPY`/`MOVE` are not implemented at all |
| An offline host looks temporary, not deleted | Originals return **503** with an explanation, never 404 |
| The catalogue is browsable while the drive is offline | PROPFIND is served from SQLite, not from the disk |
| A damaged client cannot corrupt a project file | The mount is read-only, so there is no write path to corrupt |
| No whole-file staging by accident | Un-ranged reads of files > 8 MB are refused (400) |
| No path escape | Segments are validated; nothing outside a registered root is reachable |

| Not guaranteed (yet) | Why it matters |
|---|---|
| That an NLE will open/scrub through this mount | Untested with Premiere/Resolve — Gate A |
| Write access of any kind | Intentional in V1: co-editing happens in the NLE's own collaboration service, never by syncing project files through this mount |
| Locks | `LOCK`/`UNLOCK` return 501; nothing is faked |
| TLS | Not implemented; Basic credentials travel in cleartext on the LAN |
| Native panels / OS integration | None claimed |

## Why read-only is the correct V1 choice

The blueprint forbids syncing proprietary project files as documents and forbids
byte-merging them. If this mount were writable, the most likely failure in a real
studio is exactly that: an editor drops a `.prproj` or `.drp` into a shared folder and
two machines race on it. A read-only mount makes that class of data loss impossible,
and pushes all project-state mutation into each NLE's own locking/version mechanism —
which is the behaviour Gate C is supposed to validate.

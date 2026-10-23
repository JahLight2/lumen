# Use it right now

Three ways, fastest first.

## 1. Click it in the browser (0 minutes, already running)

The real host is live on **http://127.0.0.1:8765** — that is the preview panel in this
workspace. Real backend, real SQLite catalogue, real WebDAV mount. Things to try:

| What to do | Where |
|---|---|
| Browse the drive tree, open a folder, click an asset | left panel → `01_DAY1_INTERVIEW` → `A001_C003.mp4` |
| Read and post a threaded, timecoded comment | right panel → **Comments** |
| Make a select and see it land in the selects tray | **Selects** |
| Mint a client review link | **Share** → pick expiry → copy `/r/…` link |
| Open the client view like a producer would on a phone | paste the `/r/{token}` link into a new tab |
| Mount the drive read-only | `/dav/` (Finder: Go → Connect to Server) |

A no-passphrase guest link already minted for you:
`/r/ylgisx46sX8S1UJkPbyVa_1XIDbPS5er` (72 h, 5 shared comments, internal one hidden).

## 2. Open the snapshot on your phone (0 minutes, no server)

`LUMEN-DEMO.html` — one self-contained file. AirDrop it, WhatsApp it, or open it
from Files. It shows the captured interface and refuses every write on purpose
(it is a snapshot, not the product). Guest page: open the same file and add
`#guest` to the address.

## 3. Run the real thing on your own Mac or Windows machine (5 minutes)

This is the only way to see it with **your own footage**.

1. Get the zip `lumen-host-2026-10-23.zip` from this workspace and unzip it
   anywhere (Desktop is fine). No admin rights, no installs, no Python packages.
2. Make sure **Python 3.10+** is installed:
   `python3 -V` (macOS/Linux) or `py -3 -V` (Windows). If it is missing, install
   from python.org — tick *Add python.exe to PATH* on Windows.
3. Double-click:
   * macOS → **start-lumen.command** (first time: right-click → Open, because
     macOS quarantines downloaded scripts)
   * Windows → **start-lumen.bat**
   It seeds a small sample drive on first run and opens the app at
   `http://127.0.0.1:8765`.
4. Point it at your real footage instead:

   ```bash
   python3 -m lfmc.cli add-root "/Volumes/MySSD/Shoot_Day1" --label "Shoot Day 1" --scan
   python3 -m lfmc.cli status
   ```

   Windows: `py -3 -m lfmc.cli add-root "D:\Shoot_Day1" --label "Shoot Day 1" --scan`

   Then reload the browser. The tree, sizes and availability flags come from the
   real files; nothing is uploaded anywhere.

### Let a teammate or client look at it from another device

Same Wi-Fi, same machine, keep the window open:

```bash
python3 -m lfmc.cli serve --host 0.0.0.0 --port 8765 --demo
```

Then, on their phone: `http://<your-computer-ip>:8765/` (macOS: *System Settings →
Network* shows the IP; Windows: `ipconfig`). For a client, do **not** give them the
app URL — mint a review link in **Share** and send the `/r/{token}` address instead.
The guest page is the accountless one: no login, one asset, its own comments.

**Not on the public internet.** That is deliberate: with `--host 0.0.0.0` the host is
reachable on your LAN only, and originals stream from your drive while the window is
open. For remote reviewers use Tailscale or a VPN in front of it — never a public
tunnel, because that is how unattended footage ends up on the open web.

### On the footage

Originals are read from the drive in place. Disconnect the drive and the catalogue,
thumbnails metadata and comments stay browsable, but playback and "original
available" will honestly report that the media is offline. That distinction is the
whole point of the product, so try it: unplug, reload, watch the availability flag
flip, plug back in.

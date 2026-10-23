# Configuration

Three files, from most to least often changed:

- `sources.txt`: which playlists and radio stations to check. The easiest way to choose them is on the Dashboard ([choosing playlists](#choosing-channel-sources-on-the-dashboard)). In the file:
  - Remove `#` to turn a line on. Add your own URLs or file paths, one per line.
  - Radio lines (`radio-browser: country=US top=100`) are explained in the file. For a playlist of radio stations that doesn't mark them as radio, add the word `radio` after its name (`https://example.com/jazz.m3u jazz radio`), and its stations appear under Radio.
  - With Docker, a playlist file must be inside the kia folder (e.g. `playlists/mine.m3u`), because kia can't see the rest of your computer.
  - Better still, keep your choices in your own copy, set with `KIA_SOURCES_FILE` below, so updating kia never touches them.
- `.env`: how kia runs on this machine (Docker only). Copied from `.env.example`; never committed.
- `profiles/profiles.toml`: smaller playlists for people and devices, made from the full list. The Dashboard writes it for you; you can also edit it by hand. See [profiles](profiles.md).
- `kia.toml`: fine-tuning. The defaults suit most people.

When a setting is in both `.env` and `kia.toml`, `.env` wins. The two "exclude" lists are the exception: `.env` adds to `kia.toml`'s list instead of replacing it.

To see the settings kia is running with, open **System** on the Dashboard, or run `docker compose exec kia python3 -m kia settings`. Both list each setting with where to change it. Apart from the playlists kia checks ([below](#choosing-channel-sources-on-the-dashboard)), settings can't be changed from the Dashboard.

## `.env`

| Setting | Default | What it does |
|---|---|---|
| `KIA_PUBLIC_URL` | empty | How Kodi reaches this machine, e.g. `http://192.168.1.20:8780`. Written into the playlist so Kodi finds the guide. |
| `KIA_BIND` | `0.0.0.0` | `0.0.0.0` = any device on your network; `127.0.0.1` = this machine only. |
| `KIA_PORT` | `8780` | Port for the playlist and guide. |
| `KIA_RUN_AT` | `04:00` | Daily refresh time (24-hour clock). A run takes up to an hour. |
| `KIA_GUIDE_REFRESH_HOURS` | 4 | Hours between quick guide refreshes, which keep guides that list only the next few hours current. They download only the guides kia reads itself, in seconds. `0` turns them off. |
| `TZ` | `Etc/UTC` | Your time zone, e.g. `America/New_York`. |
| `PUID`, `PGID` | `1000` | The user and group that own the files kia writes. Use the numbers `id -u` and `id -g` print, so you can open and delete those files yourself. |
| `KIA_SOURCES_FILE` | `sources.txt` | Use your own list instead, e.g. `sources.local.txt` (copy `sources.txt` to start). git ignores it, so updates never conflict with your choices. |
| `KIA_PROFILES_FILE` | `profiles/profiles.toml` | Your playlists for people and devices; see [profiles](profiles.md). With Docker it must be in the `profiles` folder, the one kia can write. |
| `KIA_HTTPS_PORT` | `8743` | The secure Dashboard, where you make changes. Playlists and guides stay on `KIA_PORT`. See [the secure Dashboard](secure-page.md). |
| `KIA_TLS_NAMES` | the host in `KIA_PUBLIC_URL` | Names and addresses the certificate covers, comma-separated, e.g. `kia.home.lan,192.168.1.20`. The Dashboard links to the first. |
| `KIA_ADMIN_PASSWORD` | made by kia | The password for making changes on the Dashboard. When it's empty, kia makes one and saves it in `state/admin-password.txt`, and you can change it on the Dashboard. |
| `KIA_EDITING` | true | `false` turns off changes from the Dashboard. |
| `KIA_PLUTO_RELAY` | true | Pluto TV channels play through kia, so they keep going across ad breaks. `false` gives players Pluto's own links, which freeze or jump back at breaks in most players. See [Pluto TV](how-it-works.md#pluto-tv). |
| `KIA_RUN_ON_START` | false | Also refresh whenever kia starts. Without it, kia still refreshes at start when it missed the last nightly run (the computer was off). |
| `KIA_UPSTREAM_URL` | | A home kia server to copy the guide from (see [setups](setups.md#laptop-that-travels-with-a-home-server)). |
| `KIA_UPSTREAM_FALLBACK` | `grab` | When the upstream can't be reached: `grab` the guide here, or use the `cached` copy. |
| `KIA_NTFY_URL` | | Send alerts to your phone through ntfy (see [alerts](alerts.md)). |
| `KIA_SMTP_HOST`, `KIA_SMTP_PORT`, `KIA_SMTP_SECURITY` | , 587, `starttls` | Your email provider's server for [email alerts](alerts.md#email). `KIA_SMTP_SECURITY` is `starttls` (port 587), `ssl` (port 465), or `none` (port 25). |
| `KIA_SMTP_USER`, `KIA_SMTP_PASSWORD` | | The account kia signs in to the email server with. Many providers want an app password here. |
| `KIA_EMAIL_TO`, `KIA_EMAIL_FROM` | , `KIA_SMTP_USER` | Who gets email alerts (comma-separated), and the address they come from. |
| `KIA_ALERT_COMMAND` | | A command to run for each alert. |
| `KIA_ALERT_EVENTS` | all but `daily_summary` and `pluto_fallback` | Which events notify you, comma-separated. |
| `KIA_EPG_DAYS` | 3 | Days of program guide. |
| `KIA_PARALLEL_SITES` | 4 | Guide sites downloaded at once. Each peaks around 0.7 GB of memory. |
| `KIA_CHECK_CONCURRENCY` | 20 | Streams tested at once. |
| `KIA_MEMORY_LIMIT` | `4g` | Memory limit for kia. |
| `KIA_LOG_MAX_SIZE`, `KIA_LOG_MAX_FILES` | `10m`, `3` | How much of each container's log Docker keeps (what `docker compose logs` shows): this many files of this size, so at most 30 MB per container by default. The oldest lines go first. |
| `KIA_EXCLUDE_SITES` | | Guide sites to skip, comma-separated. Site names are listed in `mapping-report.html`. |
| `KIA_EXCLUDE_GROUPS` | | Channel categories to leave out, e.g. `Religious,Shop`. |
| `KIA_PLACEHOLDERS` | true | Show the channel's name in the guide when there's no guide data. |
| `KIA_RADIO_PLACEHOLDERS` | false | The same for radio stations, so Kodi's radio guide lists each station's name. Almost no station has guide data, so this fills the radio guide with name blocks. |
| `KIA_EPG_GIT_URL`, `KIA_EPG_GIT_REF` | iptv-org/epg, `latest` | Use a fork of iptv-org/epg, or pin a branch, tag, or commit. See [versions](#versions). |
| `KIA_BASE_IMAGE`, `KIA_NODE_VERSION`, `KIA_NGINX_IMAGE` | `cgr.dev/chainguard/wolfi-base:latest`, `24`, `nginx:alpine` | kia's base image, the Node.js major version on it, and the web server's image. See [versions](#versions). |
| `COMPOSE_PROFILES` | | Extra services to start with kia, comma-separated: `plex` starts Threadfin, for [Plex](plex.md) (`plex,plex-2` starts two, for more than 480 channels); `plex-server` adds Plex itself ([the home TV box](cookbooks/plex-box.md)); `monitoring` adds Prometheus and Grafana, and `monitoring-node` node-exporter (Linux), `monitoring-gpu` the graphics card's exporter (NVIDIA, Linux), and `monitoring-disks` the disks' health exporter (Linux) ([graphs](cookbooks/observability.md)). |
| `KIA_THREADFIN_PORT`, `KIA_THREADFIN_2_PORT` | `34400`, `34401` | Threadfin's web pages, and the tuner addresses Plex uses. |
| `KIA_THREADFIN_IMAGE` | `fyb3roptik/threadfin:1.2.37` | Threadfin's version. |
| `KIA_THREADFIN_PLAYLIST`, `KIA_THREADFIN_2_PLAYLIST` | `plex` (or `plex-1`), `plex-2` | The kia playlist each Threadfin reads. kia sets Threadfin up with it the first time Threadfin starts, and never changes Threadfin's settings after that. |
| `KIA_PLEX_URL`, `KIA_PLEX_TOKEN` | | Plex's address (such as `http://192.168.1.20:32400`) and a Plex token, for `kia plex-tuners`, which adds the Threadfins to Plex ([Plex](plex.md#let-kia-add-them)). The token is like a password for your Plex account: keep `.env` private. |
| `PLEX_CLAIM` | | For Plex in the same stack ([the home TV box](cookbooks/plex-box.md)): a code from [plex.tv/claim](https://plex.tv/claim) that links the new Plex to your account on its first start. It works for 4 minutes. |
| `KIA_PLEX_ADVERTISE` | | The address Plex apps on your network use to reach Plex in the stack, such as `http://192.168.1.20:32400/`. |
| `KIA_PLEX_PORT` | `32400` | Plex's port, for Plex in the stack. |
| `KIA_PLEX_NAME` | `kia-tv-box` | The server's name in Plex apps, for Plex in the stack: letters, digits, and dashes. |
| `KIA_PLEX_IMAGE` | `plexinc/pms-docker:latest` | Plex's version, for Plex in the stack. |
| `KIA_PLEX_CONFIG`, `KIA_PLEX_TRANSCODE` | `plex/config`, `plex/transcode` | Where Plex in the stack keeps its settings and database, and its temporary files while converting video. Both survive restarts and updates. A name without a `/`, such as `plex-config`, is a Docker volume instead of a folder ([on a Mac](#on-a-mac)). |
| `KIA_MEDIA_DIR` | `media` | Your movies, shows, and music, for Plex or Jellyfin in the stack. They can read them but not change them. |
| `KIA_JELLYFIN_ADVERTISE` | | The address Jellyfin apps on your network use to reach Jellyfin in the stack (`jellyfin-server` profile, [Jellyfin TV box](cookbooks/jellyfin-box.md)), such as `http://192.168.1.20:8096`. |
| `KIA_JELLYFIN_PORT` | `8096` | Jellyfin's port, for Jellyfin in the stack. |
| `KIA_JELLYFIN_IMAGE` | `jellyfin/jellyfin:latest` | Jellyfin's version, for Jellyfin in the stack. |
| `KIA_JELLYFIN_CONFIG`, `KIA_JELLYFIN_CACHE` | `jellyfin/config`, `jellyfin/cache` | Where Jellyfin in the stack keeps its settings and database, and its pictures and converted video. `jellyfin-config` is a Docker volume ([on a Mac](#on-a-mac)). |
| `KIA_GRAFANA_PASSWORD` | made by Grafana | Grafana's admin password, with the `monitoring` profile ([graphs for your TV stack](cookbooks/observability.md)). When it's empty, Grafana makes one on its first start and saves it in `monitoring/grafana/admin-password.txt` ([on a Mac](#on-a-mac), inside its volume). Grafana reads it on its first start only. |
| `KIA_GRAFANA_PORT`, `KIA_PROMETHEUS_PORT` | `8788`, `8789` | Grafana's port, and Prometheus's, which is open to this computer only. |
| `KIA_NODE_EXPORTER_LISTEN` | `:9100` | Where node-exporter (`monitoring-node`) listens. The default is every address of the computer; Docker's own address, usually `172.17.0.1:9100`, keeps the computer's numbers off the rest of your network. |
| `KIA_PROMETHEUS_RETENTION` | `30d` | How long Prometheus keeps the numbers. |
| `KIA_PROMETHEUS_DATA`, `KIA_GRAFANA_DATA` | `monitoring/prometheus`, `monitoring/grafana` | Where they keep their data. `grafana-data` is a Docker volume ([on a Mac](#on-a-mac)). |
| `KIA_GRAFANA_URL` | | A Grafana elsewhere, for the Dashboard's **Observability** button. Without it, the button opens this computer's Grafana when the `monitoring` profile is on. |
| `KIA_PROMETHEUS_IMAGE`, `KIA_GRAFANA_IMAGE`, `KIA_CADVISOR_IMAGE`, `KIA_NODE_EXPORTER_IMAGE`, `KIA_SMARTCTL_IMAGE` | the latest | The monitoring images' versions. |
| `KIA_DCGM_IMAGE` | `nvidia/dcgm-exporter:4.3.1-4.4.1-ubuntu22.04` | The graphics card exporter's version (`monitoring-gpu`); NVIDIA has no `latest` tag. |
| `KIA_IMAGE` | built here | kia's published image instead of building it, e.g. `ghcr.io/southsidedean/kodi-iptv-assistant:latest`. See [the published image](#the-published-image). |

After changing `.env`, apply it with `docker compose up -d`. Add `--build` if you changed `KIA_BASE_IMAGE` or `KIA_NODE_VERSION`.

### On a Mac

Docker on a Mac runs in a small Linux machine of its own, and reaches folders on the Mac through a file share. Plex's database doesn't survive that share: within minutes of scanning a library, Plex logs errors and its database is damaged. So on a Mac, the installer sets `KIA_PLEX_CONFIG=plex-config`, `KIA_JELLYFIN_CONFIG=jellyfin-config`, and `KIA_GRAFANA_DATA=grafana-data`, which keep Plex's, Jellyfin's, and Grafana's data in Docker volumes, inside Docker's own machine. Nothing changes on Linux.

- **Grafana's password**, if you didn't choose one: `docker compose exec grafana cat /var/lib/grafana/admin-password.txt`.
- **Backups:** Docker Desktop's **Volumes** page can export a volume to a file. `kia backup` doesn't include them, the same as Plex's folder on Linux.
- **Installed on a Mac before kia 0.5.4**, with Plex in the stack? Plex there keeps its data in `plex/config`. If Plex works, you can leave it. To move Plex to a volume, keeping its link to your Plex account (it then scans your libraries again from the start), run this in the kia folder:

  ```sh
  sed -i '' 's|^KIA_PLEX_CONFIG=.*|KIA_PLEX_CONFIG=plex-config|' .env
  docker compose stop plex
  docker compose run --rm --no-deps -v "$PWD/plex/config:/old:ro" --entrypoint sh plex -c \
    'p="Library/Application Support/Plex Media Server"; mkdir -p "/config/$p" && cp "/old/$p/Preferences.xml" "/config/$p/"'
  docker compose up -d
  ```

  Then add your libraries in Plex again. The old folder stays as it was until you delete it.

## Choosing channel sources on the Dashboard

The secure Dashboard's **Sources** page has a **Channel sources** card: the playlists kia reads channels from. It lists every one kia uses, plus every one `sources.txt` offers turned off, grouped like the file. To use one, tick it; to stop, untick it. **Add a channel source** takes the address of any M3U playlist on the internet. You need the password, the same one as for [profiles](profiles.md).

**Check now** tests every stream in one source right away and shows how many work, with a few of the working channels' names. It takes from seconds to a few minutes, depending on the playlist's size, and changes nothing. **Stop** ends a check that's taking too long.

kia doesn't change your sources file (with Docker, it can't), so it keeps the choices made on the page in `state/sources.json` and applies them on top of the file at every run. The file stays yours to edit by hand. If you use your own copy (`KIA_SOURCES_FILE`), the page still offers everything in `sources.txt`. Like changes to the file, choices made on the page take effect at the next refresh.

## When changes take effect

kia reads `sources.txt` (or your own copy), the playlists chosen on the Dashboard, and `kia.toml` at the start of each run, so changes to them show up after the next nightly run. Changes to `.env` need `docker compose up -d` first. Kodi then picks up the new list within two hours (its refresh interval), or when it restarts.

To refresh now instead of waiting:

1. Run `docker compose exec kia python3 -m kia run`. It takes as long as a nightly run, up to an hour. If a refresh is already running (the nightly one, say), it says so and stops: one refresh at a time.
2. In Kodi, restart Kodi. If channels you removed still show, clear Kodi's TV data as described in [troubleshooting](troubleshooting.md#old-missing-or-mixed-up-channels).

To try out a change first, `docker compose exec kia python3 -m kia check --limit 50` tests the first 50 streams. Test runs like this one (also with `-s` or `-u`) write their results to `work/check-test/` and leave the playlist Kodi uses alone.

## Favorites and your own channel numbers

kia numbers channels automatically and keeps each number steady. Both favorites and your own numbers are easiest to set on the secure Dashboard:

1. Open the secure Dashboard (`https://<your address>:8743/`) and choose **Everything** under your playlists.
2. Enter the password at the top of the channel list.
3. Choose a channel's star to make it a favorite. Favorites come first in every playlist, and get a Favorites category that Kodi and most players show as a group. A [profile](profiles.md) can use it too, for a playlist of just your favorites.
4. To change a channel's number, choose its number, type the new one, and press Enter. The channel then has that number in every playlist that doesn't [number from 1](profiles.md#what-a-profile-can-say). A channel that already had it moves to the next free number. To undo a number you chose, clear the box: the channel keeps the number, but a number you choose for another channel can then take it.

The playlists change right away, and Kodi picks them up within two hours or when it restarts. kia keeps these choices in `state/channels.json`. Stars also work on a profile's page; numbers only on Everything, so they're the same everywhere.

### Gaps in the numbers, and renumbering

A channel that drops out keeps its number for 30 days in case it comes back, and its number isn't given to another channel, so that anything Kodi linked to it (recording timers, Kodi's own favorites) never ends up on the wrong channel. Over months that leaves gaps, and new channels get ever higher numbers. Two things tidy that up:

- **Renumber everything**, on the secure page's Everything list (or `docker compose exec kia python3 -m kia renumber`), numbers every channel 1, 2, 3 down the list once: TV and radio apart, favorites first, then by name. Numbers you chose keep theirs. From then on the numbers stay fixed again. Afterwards, clear Kodi's TV data on each device (*Settings → PVR & Live TV → General → Clear data*), so Kodi doesn't mix up old and new numbers.
- **Reusing old numbers**: set `recycle_numbers_after_days` under `[output]` in `kia.toml`, for example to `90`. A new channel then takes the lowest number whose channel was last seen at least that many days ago, instead of a new one. It's off by default, and must be 60 or more, so Kodi has long forgotten the old channel.

You can also choose a channel's number in a playlist file of your own: list the channel in a playlist file of your own in the `playlists/` folder, add `tvg-chno="<number>"` to its `#EXTINF` line, and add the file to your sources list. For example, `playlists/favorites.m3u` with two radio stations at the top of the radio list:

```text
#EXTM3U
#EXTINF:-1 tvg-chno="1" radio="true",KINK
https://playerservices.streamtheworld.com/api/livestream-redirect/KINK.mp3
#EXTINF:-1 tvg-chno="2" radio="true",KINK DNA
https://playerservices.streamtheworld.com/api/livestream-redirect/KINK_DNA.mp3
```

and in your sources list:

```text
playlists/favorites.m3u   favorites
```

Channels that already had those numbers move to the next free ones, and the log says which. Numbers in playlists from the internet are ignored, so they can't take yours. If you remove a `tvg-chno` later, the channel keeps the number it has. A number in your own playlist file wins over one chosen on the Dashboard.

## Backing up, and moving to another computer

One command saves everything you've set up in one file: `.env`, `kia.toml`, your sources file, your own playlists, profiles, and `state/` (the password, channel numbers, favorites, the playlists chosen on the Dashboard, and stream history), plus the secure page's certificate and Threadfin's settings (`threadfin/`, `threadfin-2/`). Channel lists and guides aren't in it; kia rebuilds them at its next refresh. Plex's own library and your media aren't in it either: back those up the way you back up other files.

```sh
docker compose exec kia python3 -m kia backup
```

The file goes in `work/backups/` in the kia folder, named with the date, such as `kia-backup-20261020-221500.tar.gz`. It holds your password and the certificate's private key, so keep copies somewhere private. Without Docker, run `PYTHONPATH=src python3 -m kia backup` in the kia folder.

To restore, for example on a new computer after [installing kia](install.md): copy the file into the kia folder, then run

```sh
docker compose down
docker compose run --rm --no-deps -v "$PWD:/restore" kia restore /restore/kia-backup-20261020-221500.tar.gz --to /restore
docker compose up -d
```

A restore first saves the settings that are there now, in `work/backups/before-restore-<date>.tar.gz`, so you can undo it the same way. If the new computer has a different address, check `KIA_PUBLIC_URL` in `.env` afterwards.

## Uninstalling

To remove kia, in the kia folder:

```sh
docker compose down --rmi all --volumes
```

(`--volumes` also removes the Docker volumes for Plex, Jellyfin, and Grafana [on a Mac](#on-a-mac).) Then delete the kia folder. That also deletes your settings and choices, so [back up](#backing-up-and-moving-to-another-computer) first if you might come back.

**Before you delete it, check what else is in it.** With Plex in the stack ([the home TV box](cookbooks/plex-box.md)), the kia folder can hold your movies, shows, and music (`media/`, unless `KIA_MEDIA_DIR` points elsewhere) and Plex's library and watch history (`plex/`). With monitoring on, it holds the graphs' history (`monitoring/`). Move anything you want to keep out of the folder first. If you opened ports in a Linux firewall, close them with `sudo ufw delete allow 8780/tcp` and `sudo ufw delete allow 8743/tcp`. On each device, remove kia's address from the player (in Kodi, disable or uninstall IPTV Simple Client). Without Docker, `scripts/install-service.sh --uninstall` removes the service first.

## Versions

By default kia uses the latest version of each thing it depends on:

| Part | Default | Updated |
|---|---|---|
| iptv-org/epg (the guide downloader) | `latest`, its main branch | Every night, so fixes for guide sites arrive by themselves |
| kia's base image | [Wolfi](https://github.com/wolfi-dev) (`cgr.dev/chainguard/wolfi-base:latest`), with Python, ffmpeg, and Node.js 24 (`KIA_NODE_VERSION`) | When you update kia (below) |
| nginx (the web server) | `nginx:alpine`, the latest release | When you update kia (below) |

To update kia and both images, run `scripts/update.sh` in the kia folder ([updating](../README.md#updating-and-stopping)).

To stay on the versions you have now, run `scripts/pin-versions.sh` in the kia folder. It backs up `.env` and writes the versions of iptv-org/epg, the base image, and nginx into it. Threadfin is already pinned (`KIA_THREADFIN_IMAGE`); Plex and the monitoring images follow their latest release unless you set `KIA_PLEX_IMAGE`, `KIA_PROMETHEUS_IMAGE`, `KIA_GRAFANA_IMAGE`, `KIA_CADVISOR_IMAGE`, or `KIA_NODE_EXPORTER_IMAGE` to a version. `scripts/pin-versions.sh --unpin` goes back to the latest. To pin versions by hand, set them in `.env`: an iptv-org/epg tag or commit in `KIA_EPG_GIT_REF`, an exact image in `KIA_BASE_IMAGE` or `KIA_NGINX_IMAGE`, by digest (e.g. `nginx:alpine@sha256:...`), and a Node.js major version in `KIA_NODE_VERSION` (the versions each kia release was tested with are in the [changelog](../CHANGELOG.md)). Pinned versions don't receive fixes until you change them.

Both containers run locked down. They have no extra system privileges and a read-only system. kia can write only to `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/`, and the web server sees only `output/`. That's because the guide downloader is third-party code that changes often. It runs inside the kia container, in Node.js's sandbox, where it can't change kia itself, your settings, or anything else on the computer, and can't read your passwords. For the same reason, keep the folder settings in `kia.toml` as they are when you use Docker.

### The published image

Instead of building kia on your computer, you can use the image published with each release, for 64-bit Intel and AMD computers and 64-bit ARM ones (Raspberry Pi 4 and 5, Apple silicon Macs). It saves the build, which takes a few minutes and more on a Raspberry Pi. In `.env`:

```ini
KIA_IMAGE=ghcr.io/southsidedean/kodi-iptv-assistant:latest
```

Then start it with `docker compose up -d`, and update it with `scripts/update.sh`, which pulls the image instead of building it.

`latest` follows each release. To stay on one, use its version instead, such as `:0.4.0`, and move the kia folder to the same release with `scripts/update.sh v0.4.0`: the image doesn't include the settings files and the web server's settings in the folder, so the two should match. `:main` has what's merged since the last release (`scripts/update.sh --main`). Running `docker compose up -d --build` builds kia locally again, under the image's name, until the next `docker compose pull`.

Each image is scanned for known vulnerabilities before it's published, and from 0.5.1 on it comes with a signed record of how and where it was built. To check that an image really came from kia's repository, with GitHub's `gh` command: `gh attestation verify oci://ghcr.io/southsidedean/kodi-iptv-assistant:latest --owner southsidedean`.

## `kia.toml`

Each setting is explained by a comment in the file. The sections:

- `[check]`: stream testing: `merge_duplicates` (join the same channel from different playlists), `concurrency`, `per_host` (tests at once per server), `per_host_limits` (exceptions to `per_host` for some servers), `host_delay_ms` (the gap between tests on one server), `timeout` (seconds before a silent stream fails), `require_video`, `grace_failures` (how many failed nightly checks in a row a stream that used to work survives; it's dropped after the next one), `history_days`, `user_agent` (kia introduces itself as VLC by default).
- `[filters]`: leave channels out before testing: `exclude_groups`, `exclude_labels` (e.g. `"Geo-blocked"`, `"Not 24/7"`), `exclude_name_patterns` (regular expressions).
- `[output]`: where files go, `channel_numbers` (steady channel numbers), `recycle_numbers_after_days` ([reusing old numbers](#gaps-in-the-numbers-and-renumbering)), and `public_url` / `epg_url` for setups without Docker.
- `[dns]`: `verify_resolvers`, the public DNS servers used to double-check lookup failures.
- `[alerts]`: `ntfy_url`, `command`, `events`, `drop_percent`, `site_fail_runs`, and `[alerts.email]` with `host`, `port`, `security`, `user`, `password`, `to`, `from` (the same as the `.env` settings). See [alerts](alerts.md).
- `[epg]`: guide building: `days`, `max_sites_per_channel`, `prefer_sites`, `exclude_sites`, `parallel_sites`, `max_connections`, `site_connections`, `site_delay_ms`, `site_time_limit`, `accept_name_matches`, `placeholders`, `placeholder_hours`, `radio_placeholders`, `native_grabbers`, `name_matching`, `ignore_name_matches`, `playlist_guides`, `git_url`, `git_ref`, `runner`.
- `[upstream]`: `url` and `fallback`, the same as the `.env` settings above.
- `[pluto]`: `relay`, the same as `KIA_PLUTO_RELAY` above.

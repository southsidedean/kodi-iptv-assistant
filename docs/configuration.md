# Configuration

Three files, from most to least often changed:

- `sources.txt`: which playlists and radio stations to check. Remove `#` to turn a line on; add your own URLs or file paths, one per line. Radio lines (`radio-browser: country=US top=100`) are explained in the file. With Docker, a playlist file must be inside the kia folder (e.g. `playlists/mine.m3u`), because kia can't see the rest of your computer. Better still, keep your choices in your own copy, set with `KIA_SOURCES_FILE` below.
- `.env`: how kia runs on this machine (Docker only). Copied from `.env.example`; never committed.
- `kia.toml`: fine-tuning. The defaults suit most people.

When a setting is in both `.env` and `kia.toml`, `.env` wins. The two "exclude" lists are the exception: `.env` adds to `kia.toml`'s list instead of replacing it.

## `.env`

| Setting | Default | What it does |
|---|---|---|
| `KIA_PUBLIC_URL` | | How Kodi reaches this machine, e.g. `http://192.168.1.20:8780`. Written into the playlist so Kodi finds the guide. |
| `KIA_BIND` | `0.0.0.0` | `0.0.0.0` = any device on your network; `127.0.0.1` = this machine only. |
| `KIA_PORT` | `8780` | Port for the playlist and guide. |
| `KIA_RUN_AT` | `04:00` | Daily refresh time (24-hour clock). A run takes up to an hour. Set Kodi's refresh hour to match ([Kodi setup](kodi.md#3-point-it-at-the-playlist)). |
| `TZ` | `UTC` | Your time zone, e.g. `America/New_York`. |
| `PUID`, `PGID` | `1000` | The user and group that own the files kia writes. Use the numbers `id -u` and `id -g` print, so you can open and delete those files yourself. |
| `KIA_SOURCES_FILE` | `sources.txt` | Use your own list instead, e.g. `sources.local.txt` (copy `sources.txt` to start). git ignores it, so updates never conflict with your choices. |
| `KIA_RUN_ON_START` | false | Also refresh whenever kia starts. |
| `KIA_UPSTREAM_URL` | | A home kia server to copy the guide from (see [setups](setups.md#laptop-that-travels-with-a-home-server)). |
| `KIA_UPSTREAM_FALLBACK` | `grab` | When the upstream can't be reached: `grab` the guide here, or use the `cached` copy. |
| `KIA_NTFY_URL` | | Send alerts to your phone through ntfy (see [alerts](alerts.md)). |
| `KIA_ALERT_COMMAND` | | A command to run for each alert. |
| `KIA_ALERT_EVENTS` | all but `daily_summary` | Which events notify you, comma-separated. |
| `KIA_EPG_DAYS` | 3 | Days of program guide. |
| `KIA_PARALLEL_SITES` | 4 | Guide sites downloaded at once. Each peaks around 0.7 GB of memory. |
| `KIA_CHECK_CONCURRENCY` | 20 | Streams tested at once. |
| `KIA_MEMORY_LIMIT` | `4g` | Memory limit for kia. |
| `KIA_EXCLUDE_SITES` | | Guide sites to skip, comma-separated. Site names are listed in `mapping-report.html`. |
| `KIA_EXCLUDE_GROUPS` | | Channel categories to leave out, e.g. `Religious,Shop`. |
| `KIA_PLACEHOLDERS` | true | Show the channel's name in the guide when there's no guide data. |
| `KIA_RADIO_PLACEHOLDERS` | false | The same for radio stations, so Kodi's radio guide lists each station's name. Almost no station has guide data, so this fills the radio guide with name blocks. |
| `KIA_EPG_GIT_URL`, `KIA_EPG_GIT_REF` | iptv-org/epg, `latest` | Use a fork of iptv-org/epg, or pin a branch, tag, or commit. See [versions](#versions). |
| `KIA_NODE_IMAGE`, `KIA_NGINX_IMAGE` | `node:lts-alpine`, `nginx:alpine` | Docker images for kia and the web server. See [versions](#versions). |

After changing `.env`, apply it with `docker compose up -d`. Add `--build` if you changed `KIA_NODE_IMAGE`.

## When changes take effect

kia reads `sources.txt` (or your own copy) and `kia.toml` at the start of each run, so changes to them show up after the next nightly run. Changes to `.env` need `docker compose up -d` first. Kodi then picks up the new list at its refresh hour, or when it restarts.

To refresh now instead of waiting:

1. Run `docker compose exec kia python3 -m kia run`. It takes as long as a nightly run, up to an hour.
2. In Kodi, restart Kodi. If channels you removed still show, clear Kodi's TV data as described in [troubleshooting](troubleshooting.md#old-missing-or-mixed-up-channels).

To try out a change first, `docker compose exec kia python3 -m kia check --limit 50` tests the first 50 streams. Test runs like this one (also with `-s` or `-u`) write their results to `work/check-test/` and leave the playlist Kodi uses alone.

## Your own channel numbers

kia numbers channels automatically and keeps each number steady. To choose a channel's number yourself, list the channel in a playlist file of your own in the `playlists/` folder, add `tvg-chno="<number>"` to its `#EXTINF` line, and add the file to your sources list. For example, `playlists/favorites.m3u` with two radio stations at the top of the radio list:

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

Channels that already had those numbers move to the next free ones, and the log says which. Numbers in playlists from the internet are ignored, so they can't take yours. If you remove a `tvg-chno` later, the channel keeps the number it has.

## Versions

By default kia uses the latest version of each thing it depends on:

| Part | Default | Updated |
|---|---|---|
| iptv-org/epg (the guide downloader) | `latest`, its main branch | Every night, so fixes for guide sites arrive by themselves |
| Node.js (inside the kia image) | `node:lts-alpine`, the latest long-term-support release | When you update kia (below) |
| nginx (the web server) | `nginx:alpine`, the latest release | When you update kia (below) |

To update kia and both images:

```sh
git pull
docker compose build --pull
docker compose pull --ignore-buildable
docker compose up -d
```

To stay on a version you've checked, pin it in `.env`: an iptv-org/epg tag or commit in `KIA_EPG_GIT_REF`, and an exact image in `KIA_NODE_IMAGE` or `KIA_NGINX_IMAGE`, e.g. `node:22.23.3-alpine3.24` (the versions each kia release was tested with are in the [changelog](../CHANGELOG.md)). An image can also be pinned by digest, e.g. `nginx:alpine@sha256:...`. Pinned versions don't receive fixes until you change them.

Both containers run locked down. They have no extra system privileges and a read-only system. kia can write only to `output/`, `state/`, `work/`, and `vendor/`, and the web server sees only `output/`. That's because the guide downloader is third-party code that changes often. It runs inside the kia container, where it can't change kia itself, your settings, or anything else on the computer. For the same reason, keep the folder settings in `kia.toml` as they are when you use Docker.

## `kia.toml`

Each setting is explained by a comment in the file. The sections:

- `[check]`: stream testing: `concurrency`, `per_host` (tests at once per server), `timeout` (seconds before a silent stream fails), `require_video`, `grace_failures` (how many failed nightly checks in a row a stream that used to work survives; it's dropped after the next one), `history_days`, `user_agent` (kia introduces itself as VLC by default).
- `[filters]`: leave channels out before testing: `exclude_groups`, `exclude_labels` (e.g. `"Geo-blocked"`, `"Not 24/7"`), `exclude_name_patterns` (regular expressions).
- `[output]`: where files go, `channel_numbers` (steady channel numbers; delete `state/channel-numbers.json` to renumber everything), and `public_url` / `epg_url` for setups without Docker.
- `[dns]`: `verify_resolvers`, the public DNS servers used to double-check lookup failures.
- `[alerts]`: `ntfy_url`, `command`, `events`, `drop_percent`, `site_fail_runs`. See [alerts](alerts.md).
- `[epg]`: guide building: `days`, `max_sites_per_channel`, `prefer_sites`, `exclude_sites`, `parallel_sites`, `max_connections`, `placeholders`, `placeholder_hours`, `radio_placeholders`, `native_grabbers`, `git_url`, `git_ref`, `runner`.
- `[upstream]`: `url` and `fallback`, the same as the `.env` settings above.

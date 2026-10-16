# Configuration

Three files, from most to least often changed:

- `sources.txt`: which playlists and radio stations to check. Remove `#` to turn a line on; add your own URLs or file paths, one per line. Radio lines (`radio-browser: country=US top=100`) are explained in the file.
- `.env`: how kia runs on this machine (Docker only). Copied from `.env.example`; never committed.
- `kia.toml`: fine-tuning. The defaults suit most people.

When a setting is in both `.env` and `kia.toml`, `.env` wins. The two "exclude" lists are the exception: `.env` adds to `kia.toml`'s list instead of replacing it.

## `.env`

| Setting | Default | What it does |
|---|---|---|
| `KIA_PUBLIC_URL` | | How Kodi reaches this machine, e.g. `http://192.168.1.20:8780`. Written into the playlist so Kodi finds the guide. |
| `KIA_BIND` | `0.0.0.0` | `0.0.0.0` = any device on your network; `127.0.0.1` = this machine only. |
| `KIA_PORT` | `8780` | Port for the playlist and guide. |
| `KIA_RUN_AT` | `04:00` | Daily refresh time (24-hour clock). |
| `TZ` | `UTC` | Your time zone, e.g. `America/New_York`. |
| `PUID`, `PGID` | `1000` | Owner of the files kia writes (`id -u`, `id -g`). |
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
| `KIA_EXCLUDE_SITES` | | Guide sites to skip, comma-separated. Site names are listed in `mapping-report.md`. |
| `KIA_EXCLUDE_GROUPS` | | Channel categories to leave out, e.g. `Religious,Shop`. |
| `KIA_PLACEHOLDERS` | true | Show the channel's name in the guide when there's no guide data. |
| `KIA_EPG_GIT_URL`, `KIA_EPG_GIT_REF` | iptv-org/epg, `master` | Use a fork of iptv-org/epg, or pin a branch, tag, or commit. |
| `KIA_NODE_IMAGE`, `KIA_NGINX_IMAGE` | tested versions | Docker image versions. |

After changing `.env`, apply it with `docker compose up -d`. Add `--build` if you changed `KIA_NODE_IMAGE`.

## `kia.toml`

Each setting is explained by a comment in the file. The sections:

- `[check]`: stream testing: `concurrency`, `per_host` (tests at once per server), `timeout` (seconds before a silent stream fails), `require_video`, `grace_failures` (how many nightly failures in a row before a stream that used to work is dropped), `history_days`, `user_agent` (kia introduces itself as VLC by default).
- `[filters]`: leave channels out before testing: `exclude_groups`, `exclude_labels` (e.g. `"Geo-blocked"`, `"Not 24/7"`), `exclude_name_patterns` (regular expressions).
- `[output]`: where files go, and `public_url` / `epg_url` for setups without Docker.
- `[dns]`: `verify_resolvers`, the public DNS servers used to double-check lookup failures.
- `[alerts]`: `ntfy_url`, `command`, `events`, `drop_percent`, `site_fail_runs`. See [alerts](alerts.md).
- `[epg]`: guide building: `days`, `max_sites_per_channel`, `prefer_sites`, `exclude_sites`, `parallel_sites`, `max_connections`, `placeholders`, `placeholder_hours`, `radio_placeholders`, `native_grabbers`, `git_url`, `git_ref`, `runner`.
- `[upstream]`: `url` and `fallback`, the same as the `.env` settings above.

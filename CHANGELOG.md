# Changelog

## 0.1.1 (2026-10-19)

Fixes from an independent review of 0.1.0, locked-down containers, and versions that follow the latest releases by default. New: steady channel numbers, a status page, and reports you can read in a browser.

### Upgrading from 0.1.0

1. In the kia folder, run:

   ```sh
   git pull
   docker compose build --pull
   docker compose pull --ignore-buildable
   docker compose up -d
   ```

   If `git pull` stops because you edited `sources.txt` or `kia.toml`, move your playlist choices to your own copy first ([configuration](docs/configuration.md)), then run `git checkout sources.txt kia.toml` and pull again.
2. If you changed the folder settings in `kia.toml` (`dir`, `state_dir`, `repo_dir`, `work_dir`), set them back. kia can now write only to its standard folders.
3. In Kodi, on each device, turn on IPTV Simple Client's daily refresh and Kodi's *Use channel numbers from backend* ([Kodi setup](docs/kodi.md), steps 3 and 5).

The first run after upgrading numbers every channel, in alphabetical order, so channel numbers change once. After that they stay put. `http://<your address>:8780/` shows the new status page; links straight to `curated.m3u` and `guide.xml.gz` work as before.

### Kodi

- Channels keep the same number from night to night (`tvg-chno`), instead of being renumbered whenever a stream drops out or a playlist is added. A channel that comes back within a month gets its old number. Turn on Kodi's *Use channel numbers from backend* to see them ([Kodi setup](docs/kodi.md)). TV and radio are numbered separately. To choose numbers yourself, give channels a `tvg-chno` in a playlist file of your own ([how](docs/configuration.md#your-own-channel-numbers)); numbers from internet playlists are ignored.
- The Kodi setup now includes IPTV Simple Client's daily refresh (*Auto refresh mode: Once per day*). Without it, Kodi loads the list and guide only when it starts, so devices that sleep instead of shutting down kept old data.

### Radio

- Your own playlist files can live in a `playlists/` folder, which git ignores. A station marked `radio="true"` there shows under Radio, so you can add stations radio-browser.info lists badly, such as KINK's theme channels.
- `KIA_RADIO_PLACEHOLDERS=true` in `.env` fills Kodi's radio guide with each station's name, as TV channels without guide data already do.

### Status page

- `http://<your address>:8780/` now shows a status page instead of a file list: when kia last refreshed, whether it worked, the next refresh, TV, radio, and guide counts, the last run's alerts, links to the reports, and links to the documentation for setup and troubleshooting. `status.json` has the same information for scripts and monitoring tools.
- Each report now also comes as an HTML page that reads well in a browser, with a filter box on long tables (the stream report lists every channel). The Markdown versions are still written.

### Versions

- kia now uses the latest Node.js LTS (`node:lts-alpine`), the latest nginx (`nginx:alpine`), and the latest iptv-org/epg (`KIA_EPG_GIT_REF=latest`, its default branch) unless you pin a version in `.env`. Updating kia now pulls the newest images too. Tested with Node.js 24.22.0 and nginx 1.31.7.

### Security

- Both containers run read-only, with no extra privileges. kia sees the project folder read-only and writes only to `output/`, `state/`, `work/`, and `vendor/`, so the third-party guide downloader can't change kia or its settings. The web server sees only `output/` and doesn't follow symbolic links.
- Only network streams (http, https, rtmp, rtsp) are handed to ffprobe. Guide site names are checked before they're used as file names. Only the `#KODIPROP` lines that choose a stream's player are copied from playlists. Git hooks are turned off in kia's copy of iptv-org/epg.

### Fixes

- A dropped connection while copying the guide from an upstream server no longer crashes the run or leaves a partial guide.
- A guide site with only old listings no longer beats the placeholder that shows the channel's name.
- The guide is still built when no channel matches a guide site, when GitHub can't be reached, when installing the downloader fails, or when one site's guide file is damaged.
- A mistyped `KIA_NTFY_URL` is logged instead of crashing the alert, and the run.
- A single failed name lookup during a guide download no longer raises a DNS alert; names are tried again first.
- A site whose own DNS servers send broken IPv6 answers is no longer reported as a problem with your network's DNS. When this computer's DNS answers the site's IPv4 lookup, kia knows your DNS works.
- On the night clocks change for daylight saving time, the refresh runs at `KIA_RUN_AT` instead of an hour early or late.
- DNS double-checks handle international names (such as `bücher.de`), and names that can't exist are reported as invalid instead of blaming the public DNS servers.
- Playlists and sources files that start with a byte-order mark (some Windows editors add one) are read correctly.
- Reports are replaced in one step, so a browser never shows a half-written one.
- The second try for slow streams keeps the limit of two tests per server.
- Test runs (`kia check` with `--limit`, `-s`, or `-u`) write to `work/check-test/` and leave the live playlist alone.

### Docs

- New: a troubleshooting page, organized by what you see, linked from the README and the status page. Also new: when changes take effect and how to refresh right away, versions and how to pin them, and checking the guide address when the guide stays empty.
- Fixed: the setup recipes no longer put comments on the same line as settings, which Docker read as part of the value. The quick start uses your own copy of `sources.txt`. Also corrected: the `grace_failures` wording, `PUID`/`PGID`, installing `curl` before Docker on Linux, IPTV Simple Client from Ubuntu and Debian packages, and running without Docker.

## 0.1.0 (2026-10-16)

The first release. kia turns free IPTV playlists into a working channel list with a matching program guide for Kodi, refreshed every night and served on your network.

### Channel list

- Tests every stream in the playlists you choose with ffprobe and keeps one working stream per channel, preferring higher resolution and faster response.
- Keeps a stream that worked before until it fails several nightly checks in a row, since free streams often drop out for a while.
- Introduces itself to servers as VLC, the player most widely accepted, and tests at most two streams per server at a time. Streams that fail a name lookup or time out get a second, slower try.
- `sources.txt` ships with a checked catalog of public playlists (iptv-org countries, languages, categories, states, and providers, plus Free-TV), with the USA list on. `KIA_SOURCES_FILE` lets each install keep its own list that git won't touch.
- Internet radio from radio-browser.info: lines such as `radio-browser: language=english top=100` add the most popular working stations, which Kodi shows under Radio.

### Program guide

- Matches channels to guide sources from iptv-org/epg using the playlist's own channel IDs, downloads guide data from several sites, and keeps the best source for each channel.
- Reads i.mjh.nz's guide files (Pluto TV, Plex, Samsung TV Plus, Roku) directly, in seconds instead of about 25 minutes.
- Channels with no guide data show their name in the guide instead of a blank row.
- The playlist carries the guide's address, so Kodi needs only one setting: the playlist URL.

### Running it

- Docker Compose setup with a small web server, configured through `.env`. Tested on Linux and on macOS (Apple silicon).
- Runs daily at a set time and catches up after a computer wakes from sleep.
- Upstream mode for laptops: test streams locally and copy the guide from a home kia server when it can be reached.
- `kia serve` for installs without Docker.

### Alerts

- Tells a local DNS failure apart from a dead stream by checking public DNS.
- Optional notifications through ntfy or any command, for a failed nightly run, local DNS trouble, sharp drops in channels or guide coverage, and guide sites that keep failing. `kia test-alert` checks the setup.

### Known limitations

- About half of the channels in the US list have no guide data from any source yet.
- Running kia itself needs macOS or Linux. Kodi on any platform, including Windows, can use it.
- Radio stations rarely have guide data.

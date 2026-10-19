# Changelog

## 0.1.1 (unreleased)

Fixes from an independent review of 0.1.0, locked-down containers, and versions that follow the latest releases by default.

### Kodi

- Channels keep the same number from night to night (`tvg-chno`), instead of being renumbered whenever a stream drops out or a playlist is added. A channel that comes back within a month gets its old number. Turn on Kodi's *Use channel numbers from backend* to see them ([Kodi setup](docs/kodi.md)). TV and radio are numbered separately.
- The Kodi setup now includes IPTV Simple Client's daily refresh (*Auto refresh mode: Once per day*). Without it, Kodi loads the list and guide only when it starts, so devices that sleep instead of shutting down kept old data.

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
- A site whose own DNS servers send broken IPv6 answers is no longer reported as a problem with your network's DNS. kia's public double-check now looks up IPv6 as well as IPv4.
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

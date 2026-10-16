# Changelog

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

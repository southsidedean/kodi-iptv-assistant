# Changelog

## 0.4.0 (unreleased)

### Upgrading from 0.3.0

Update as usual (`git pull`, then `docker compose up -d --build`), then run `docker compose up -d --force-recreate web`. The web server has a new setting for Pluto TV channels, and it runs with its old settings until it's recreated.

### Pluto TV keeps playing through ad breaks

- Pluto TV channels used to freeze, go black, or jump back minutes at ad breaks in most players, and came back only when started again. They now play through kia, which opens its own Pluto session with Pluto putting in the ads, leaves out pieces Pluto repeats or sends again, and puts every piece on one steady clock. It works the same for every player, and timeshift keeps working. See [Pluto TV](docs/how-it-works.md#pluto-tv).
- The playlist points Pluto channels at kia (`/pluto/<id>.m3u8`). `KIA_PLUTO_RELAY=false` in `.env` (or `relay = false` under `[pluto]` in `kia.toml`) goes back to Pluto's own links.
- The Details page shows the Pluto channels playing right now, with their sessions and recent problems, and `pluto.json` has the same for scripts. The home page warns you if Pluto channels are playing without kia's fix, and the new `pluto_fallback` alert (off by default) can tell you too.

### TV-only playlist

- When your list has radio stations, kia also makes a TV-only playlist, `tv.m3u`, with its own guide. Jellyfin and some other players show radio stations as TV channels, so [Jellyfin's guide](docs/jellyfin.md) now uses it instead of asking you to make a profile. A profile of your own named `tv` replaces it.

### Choosing playlists on the status page

- The secure status page's Details page lists every playlist kia uses and every one `sources.txt` offers turned off, grouped like the file, with each one's streams from the last check. Tick a playlist to use it and untick it to stop, or add any M3U playlist by its address. kia keeps these choices in `state/sources.json` and applies them on top of your sources file at every run, so the file stays yours to edit by hand. See [choosing playlists on the status page](docs/configuration.md#choosing-playlists-on-the-status-page).
- **Check now** tests every stream in one playlist straight away and shows how many work, without changing your playlists.
- The same is in the API: `GET`, `PUT`, `POST`, and `DELETE /api/sources`, and `/api/sources/check`.

### Favorites and channel numbers

- Star channels on the secure status page to make them favorites. Favorites come first in every playlist and get a Favorites category, which Kodi and most players show as a group, and which a profile can pick for a playlist of just your favorites.
- Change a channel's number on the Everything page. It works like a `tvg-chno` in your own playlist file: a channel that had the number moves to the next free one, and a number in your own file still wins.
- Playlists list TV channels first, then radio stations, each with favorites at the top. A playlist's page shows TV and radio under their own headings, in the list and in the **Next 6 hours** view.
- Both apply straight away, without waiting for a refresh. kia keeps them in `state/channels.json`. The API has `GET /api/channel-choices` and `PUT /api/channel`.

### Fixes

- kia no longer raises a `dns_local` alert when a name fails to look up and the public DNS servers it double-checks with can't be reached either. Nothing then shows that your DNS is at fault (a moment without internet is more likely), so kia notes it in the log instead.

### Status page

- The Details page lists the settings kia is running with, and where to change each one (`.env` or `kia.toml`). Passwords and alert addresses show only whether they're set. `kia settings` shows the same in a terminal, and `status.json` has it under `settings`.

## 0.3.0 (2026-10-20)

### Upgrading from 0.2.0

Update as usual (`git pull`, then `docker compose up -d --build`). Both containers are recreated, because they gained folders and a port:

- The status page is now also at `https://<your address>:8743/`, which is where you make changes. Make sure nothing else on the machine uses port 8743, or pick another with `KIA_HTTPS_PORT` in `.env`. The first visit shows a browser warning about kia's own certificate; [the secure status page](docs/secure-page.md#the-browser-warning) explains it.
- kia makes a password for changes and saves it in `state/admin-password.txt`. Set `KIA_ADMIN_PASSWORD` in `.env` to choose your own.
- kia writes two new folders, `profiles/` and `tls/`. `git pull` creates them; if you copied kia some other way, create them yourself so Docker doesn't make them owned by root.
- If the web container keeps running the old settings after an update, run `docker compose up -d --force-recreate web`.

### Playlists for people and devices

- Profiles make smaller playlists from the full one, each with its own address, such as `http://<your address>:8780/kids.m3u` for the kids' tablet. A profile picks channels by category, playlist, name, or ID, and can leave out categories, names, TV, or radio. See [profiles](docs/profiles.md).
- Make and change profiles on the status page. The form shows how many channels a playlist would have as you change it. Profiles live in `profiles/profiles.toml`, which you can also edit by hand (start from `profiles/example.toml`).
- Each profile gets its own guide with only its channels. In a test, a 90-channel kids profile's guide was 391 KB instead of 3 MB, which is much less for a slow device to load.
- `renumber = true` numbers a profile's channels from 1. Otherwise a channel has the same number in every playlist.

### Status page

- The status page is also on a secure port, `https://<your address>:8743/`, where all changes are made; playlists and guides stay on plain HTTP for the players. kia makes a self-signed certificate and renews it. To use your own, from Tailscale or Let's Encrypt for example, put it in `tls/`. `KIA_TLS_NAMES` sets the names it covers, and `kia tls` shows what's installed. See [the secure status page](docs/secure-page.md).
- kia makes an admin password on first start (in `state/admin-password.txt`), which you can change on the status page. A password in `.env` (`KIA_ADMIN_PASSWORD`) still wins. `kia password --reset` makes a new one, and `KIA_EDITING=false` turns editing off.
- The [API guide](docs/api.md) covers reading kia's status and changing profiles from scripts and other apps.
- The home page is shorter: the status, the channel counts, any alerts, and your playlists with their addresses and Copy buttons.
- Each playlist has its own page with its addresses and a filterable list of its channels, showing what's on now and next. **Next 6 hours** shows the same as a timeline.
- Guide coverage, streams by playlist, and the reports moved to a Details page.
- The kia name and logo at the top are bigger.

### Faster, steadier nightly runs

- The stream check tests up to six streams at a time on jmp2.uk, which carries the Pluto TV, Samsung TV Plus, and Plex channels, instead of two (`per_host_limits` in `kia.toml`). In a test of 60 of its streams, that took 29 seconds instead of 98, with no extra failures.
- Tests on the same server start at least 100 ms apart (`host_delay_ms`), so bursts don't set off a site's firewall.
- Guide sites can have their own number of connections and a pause between requests (`site_connections`, `site_delay_ms`). tvpassport.com now uses 100 connections with a 100 ms pause: its download took 44 seconds instead of 16½ minutes, with complete data. plex.tv gets one connection at a time, which it needs.
- A guide site that takes longer than 30 minutes is stopped, and its channels keep their previous guide (`site_time_limit`).
- ontvtonight.com is skipped by default; it sends programs without titles.

### Installing

- `scripts/install.sh` installs kia with Docker on macOS or Linux in one step: it downloads kia, finds the address and time zone, makes `.env`, opens the firewall ports if it can, starts kia, and prints the status page's address and the password. Running it again keeps your settings.
- `scripts/install-service.sh` runs kia without Docker as a service that starts with the computer: a systemd user service on Linux, a launchd agent on macOS (templates in `deploy/`). kia runs straight from its folder; there's nothing to install from Python.
- `kia daemon` takes its daily time from `KIA_RUN_AT` too.

### Docs

- [Start here](docs/start-here.md) explains what kia gives you, the three steps to get there, and the words you'll see. A [docs index](docs/README.md) lists every guide by task.
- The install guide and README make choosing playlists optional (the USA list is enough to start), and the troubleshooting guide covers the status page: the browser warning, the password, and refused changes.
- Fuller credits for the iptv-org community and the other playlist and guide sources, and a link to the Kodi Wiki.

### Guides for other players

- New setup guides for [Plex](docs/plex.md) (through Threadfin), [Jellyfin and Emby](docs/jellyfin.md), [TiviMate](docs/tivimate.md), [VLC](docs/vlc.md), and [phones and tablets](docs/phones.md), with a page to help [choose a player](docs/players.md). The Jellyfin and Threadfin steps were tested against kia.

### Sources

- `sources.txt` has new options, each tested from a US home connection: Xumo and Local Now (channels iptv-org doesn't list, without guide data) and genre radio lists from github.com/junguler (worldwide; about half the stations work, and kia keeps those).
- A playlist line in the sources file can end with `radio`, for radio playlists that don't mark their stations.

### Program guide

- `accept_name_matches` in `kia.toml` turns on "possible matches" from the mapping report that you've checked.
- Reports show times in your time zone, such as "Mon Oct 19, 15:55 CDT".

### Versions

- `scripts/pin-versions.sh` writes the exact versions you're running into `.env`, and `--unpin` goes back to the latest.

## 0.2.0 (2026-10-19)

### Upgrading from 0.1.1

In Kodi, on each device, change IPTV Simple Client's **Auto refresh mode** to *Repeated refresh* and set **Refresh interval** to 120. kia now refreshes the guide every few hours, and Kodi picks that up only if it reloads more than once a day ([Kodi setup](docs/kodi.md#3-point-it-at-the-playlist)).

### Program guide

- Between nightly runs, kia refreshes the guide every 4 hours (`KIA_GUIDE_REFRESH_HOURS`, `0` turns it off). Only the guides kia reads itself are downloaded again (i.mjh.nz, kink.nl, and the playlists' own guides), which takes seconds. Some of those list only the next few hours: i.mjh.nz's Samsung TV Plus guide reaches about 11 hours ahead. For a playlist that names many guides (Free-TV names about 100), a quick refresh downloads only the ones that had your channels the night before. `kia refresh-guide` does the same by hand. The status page shows when the guide was last updated.
- When a channel's listings end before the guide does, the rest of its row shows the channel's name instead of nothing.
- Channels that no guide site lists by ID are now matched by name: exactly the same name, in the same country, belonging to one channel only. On the test server this gave 86 more TV channels full listings (from 42% to 48%). The matches are listed in `mapping-report.html`, and `ignore_name_matches` in `kia.toml` turns off a wrong one. Looser matches appear there as suggestions only.
- kia now uses the guide a playlist names in its first line (`x-tvg-url`) for that playlist's channels. It downloads the guide, keeps the channels in your list, and compares it with iptv-org's sites. `sources.txt` adds such a list, Samsung TV Plus (USA) from apsattv.com, off by default. `playlist_guides = false` in `kia.toml` turns this off.
- The same channel listed by different playlists under different IDs now appears once. kia joins channels with exactly the same name from different playlists, keeps the ID, logo, and number from the earliest playlist in your sources list, and plays whichever copy's stream tested best. Its guide can come from any of the copies. `merge_duplicates = false` under `[check]` turns this off.
- Channel IDs that differ only in capital letters now match (`SYFY.us@East` and `Syfy.us@East`).
- Programs without a title no longer count as guide data. ontvtonight.com currently sends only times, which showed as empty blocks in Kodi; those channels now use another site or show their name.

### Radio

- For a radio station named exactly "KINK", kia reads KINK's program schedule from kink.nl, so Kodi's radio guide lists its shows and DJs.

### Status page and reports

- The status page is easier to read at a glance: a colored banner with the state of the last refresh, big numbers for channels and guide data, bars for guide coverage and for each playlist's working streams, and a Copy button for the playlist address.
- The status page and reports share one look that works on phones as well as computers. Light and dark follow the device's setting, and each system uses its own fonts, so nothing loads from the internet.
- kia has a logo: a screen showing program guide rows, with the current time marked in amber. It's the browser tab icon, and it sits at the top of the status page and every report, where clicking it returns to the status page.

### Fixes

- Accented channel names now show correctly in playlists saved as Windows-1252 instead of UTF-8, such as apsattv.com's. "AMC en Español" appeared as "AMC en Espa�ol".
- Channels that passed kia's check but wouldn't play in Kodi now play. Kodi introduced itself as Kodi, and some servers refuse that: jmp2.uk, which carries the Pluto TV, Samsung TV Plus, and Plex channels in the iptv-org lists, answered with "403 Forbidden". The playlist now tells Kodi to introduce itself the way kia did when it tested each stream (`#EXTVLCOPT:http-user-agent`).

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

- Channels keep the same number from night to night (`tvg-chno`), instead of being renumbered whenever a stream drops out or a playlist is added. A channel that comes back within a month gets its old number. Turn on Kodi's *Use channel numbers from backend* to see them ([Kodi setup](docs/kodi.md)). TV and radio are numbered separately. To choose numbers yourself, give channels a `tvg-chno` in a playlist file of your own ([how](docs/configuration.md#favorites-and-your-own-channel-numbers)); numbers from internet playlists are ignored.
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

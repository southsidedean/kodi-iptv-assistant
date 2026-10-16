# kodi-iptv-assistant

Turn free IPTV playlists into something Kodi's **IPTV Simple** client is pleasant to use with, served from one machine on your network:

1. **A curated playlist**: every stream in one or more M3U playlists (for example from [iptv-org/iptv](https://github.com/iptv-org/iptv)) is tested with `ffprobe`; only working streams make it in, one per channel.
2. **A matching program guide (EPG)**: curated channels are matched to guide sources from [iptv-org/epg](https://github.com/iptv-org/epg), guide data is grabbed, and the best source per channel is merged into one XMLTV file whose channel IDs match the playlist.
3. **Self-hosting**: Docker Compose refreshes both every day and serves them over HTTP, so every Kodi device on your network points at the same two URLs.

## Why the EPG is usually empty

Kodi links a stream to its guide by matching the playlist's `tvg-id` (e.g. `CNN.us@East`) against the XMLTV `<channel id>`. Channel lists generated with `npm run channels:parse` in iptv-org/epg have an empty `xmltv_id`, so the grabbed guide uses site-internal IDs (`2m-maroc/13668`) and nothing matches. `kia map` closes that gap by writing the playlist's `tvg-id` into each guide source.

## Quick start (Docker)

Needs Docker with Compose on a Linux box, NAS, or Raspberry Pi 4/5 (64-bit).

```sh
git clone https://github.com/southsidedean/kodi-iptv-assistant.git
cd kodi-iptv-assistant
cp .env.example .env          # set KIA_PUBLIC_URL, TZ, PUID/PGID
$EDITOR sources.txt           # pick your playlists
docker compose up -d --build
```

The first run starts right away (checking ~1,500 streams and grabbing guides takes about an hour); after that it runs daily at `KIA_RUN_AT`. Follow along with `docker compose logs -f kia`.

When it's done, these are served at `KIA_PUBLIC_URL` (e.g. `http://my-server.lan:8780/`):

| File | Contents |
|---|---|
| `curated.m3u` | Working streams, best one per channel; its header points at the guide |
| `guide.xml.gz` | Program guide for every curated channel (`guide.xml` uncompressed) |
| `report.md` | Stream check summary, common failures, channel list |
| `mapping-report.md` | Which channels matched a guide source, and which didn't |
| `guide-report.md` | Guide coverage per channel and per site |

### Setting up Kodi

On each Kodi device, install **PVR IPTV Simple Client** (*Add-ons → Install from repository → PVR clients*), then in its settings:

- **General → Location:** Remote path. **M3U playlist URL:** `http://my-server.lan:8780/curated.m3u`
- **EPG → Location:** Remote path. **XMLTV URL:** `http://my-server.lan:8780/guide.xml.gz`

Restart Kodi (or disable and re-enable the add-on). The daily refresh is picked up automatically from then on.

## Configuration

| File | What it controls |
|---|---|
| `sources.txt` | The playlists to check, one URL or local file per line, with an optional name after it |
| `kia.toml` | Check timeouts and concurrency, filters, guide settings, DNS verification, alerts |
| `.env` | Deployment: public URL, port, daily run time, timezone, file owner (not committed) |

`sources.txt` example (see iptv-org's [playlist index](https://github.com/iptv-org/iptv/blob/master/PLAYLISTS.md)):

```
https://iptv-org.github.io/iptv/countries/us.m3u
https://iptv-org.github.io/iptv/languages/eng.m3u
/home/me/my-extras.m3u8   extras
# lines starting with # are ignored
```

All sources merge into one curated playlist: shared URLs are probed once, and channels with the same `tvg-id` collapse to the best working stream.

### How streams are judged

- **ok**: ffprobe opened the stream and found a video track.
- **flaky**: failed this run, but worked on an earlier run and has failed at most `grace_failures` runs in a row. It stays in the playlist, because free streams often drop out for a while.
- **dead**: everything else.

History lives in `state/history.json`, so the playlist settles over a few daily runs. Duplicates are ranked: working beats flaky, then higher resolution, then faster response.

Run checks from the network your Kodi devices are on, since some streams are geo-blocked or block certain IP addresses.

### How the guide is built

1. **`kia map`** finds each curated channel's guide sources in iptv-org/epg (exact feed first, then other feeds of the same channel), skipping sites marked broken in its `SITES.md`, and keeps up to `max_sites_per_channel` candidates.
2. **`kia grab`** runs iptv-org/epg's grabber once per site, several sites at a time. A site that fails keeps its previous guide.
3. **`kia guide`** picks, per channel, the site with the most coverage over the next 24 hours and writes `guide.xml(.gz)`. Channels with no guide data get 2-hour placeholder blocks with the channel name, so Kodi's guide grid isn't empty (set `placeholders = false` to turn that off).

### DNS checks and alerts

Streams and guide sites are resolved with the machine's normal DNS. When a hostname fails to resolve, kia asks backup resolvers (`[dns] verify_resolvers`, default 8.8.8.8 and 1.1.1.1):

- not found there either: the stream or guide site is really gone (reported, no alert);
- found there: **your local DNS is the problem**, which would affect Kodi too, so kia raises an alert.

Alerts are logged, appended to `state/alerts.log`, make the command exit with code 3, and can run any command you set in `[alerts] command` (with `$KIA_ALERT_TITLE` and `$KIA_ALERT_MESSAGE`), for example a `curl` to ntfy, Slack, or Discord, or `mail`. A daily run that fails or crashes also alerts.

## Running without Docker

Needs Python 3.11+ (standard library only), ffmpeg, and either Docker (for the guide grabber) or Node.js 22+ with `[epg] runner = "local"`.

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/kia run                  # check, map, grab, guide
.venv/bin/kia daemon --at 04:00    # run daily
```

Individual steps:

```sh
kia check                    # every playlist in sources.txt
kia check --limit 40 -v      # quick test, log each stream
kia check -s us              # only the source named "us"
kia check --sources news.txt # a different sources file
kia check -u <url> -u <url>  # ad-hoc playlists, no file needed
kia probe <url>              # test a single stream
kia map [--update]           # match channels to guide sites (--update pulls iptv-org/epg)
kia grab [--site pluto.tv]   # grab guides
kia guide                    # merge into output/guide.xml
```

Without a venv, `PYTHONPATH=src python3 -m kia ...` works too.

## Development

```sh
.venv/bin/pytest
```

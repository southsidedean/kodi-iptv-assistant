# kodi-iptv-assistant

Tools for making free IPTV playlists usable in Kodi's **IPTV Simple** client:

1. **`kia check`**: probes every stream in one or more M3U playlists (for example from [iptv-org/iptv](https://github.com/iptv-org/iptv)) with `ffprobe`, and writes a curated playlist containing only working streams, one per channel.
2. *(planned)* **EPG mapping**: matches curated channels to guide sources in [iptv-org/epg](https://github.com/iptv-org/epg), so the guide's channel IDs match the playlist's `tvg-id`s.
3. *(planned)* **Self-hosting**: Docker Compose that refreshes the playlist and guide daily and serves both on your LAN.

## Why the EPG is usually empty

Kodi links a stream to its guide by matching the playlist's `tvg-id` (e.g. `CNN.us@East`) against the XMLTV `<channel id>`. Channel lists generated with `npm run channels:parse` in iptv-org/epg have an empty `xmltv_id`, so the grabbed guide uses site-internal IDs (`2m-maroc/13668`) and nothing matches. The mapping step closes that gap.

## Requirements

- Python 3.11+ (standard library only)
- ffmpeg / ffprobe: `brew install ffmpeg` or `sudo apt install ffmpeg`

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Usage

List one or more playlists in `sources.txt`, one URL or local file per line, with an optional name after it (see iptv-org's [playlist index](https://github.com/iptv-org/iptv/blob/master/PLAYLISTS.md)):

```
https://iptv-org.github.io/iptv/countries/us.m3u
https://iptv-org.github.io/iptv/languages/eng.m3u
/home/me/my-extras.m3u8   extras
# lines starting with # are ignored
```

Tuning (timeouts, concurrency, filters, output paths) lives in `kia.toml`, which can point at a different list with `sources_file`.

All sources merge into one curated playlist: shared URLs are probed once, and channels with the same `tvg-id` collapse to the best working stream. Then:

```sh
.venv/bin/kia check                # check every playlist in sources.txt
.venv/bin/kia check --limit 40 -v  # quick test, log each stream
.venv/bin/kia check -s us          # only the source named "us"
.venv/bin/kia check --sources news.txt  # a different sources file
.venv/bin/kia check -u <url> -u <url>   # ad-hoc playlists, no file needed
.venv/bin/kia probe <url>          # test a single stream
```

Outputs, in `output/`:

| File | Contents |
|---|---|
| `curated.m3u` | Working streams, best one per channel, ready for IPTV Simple |
| `report.md` | Summary, most common failures, list of curated channels |
| `report.json` | Every stream checked, with status, resolution, error, timing |

### How streams are judged

- **ok**: ffprobe opened the stream and found a video track.
- **flaky**: failed this run, but worked on an earlier run and has failed at most `grace_failures` runs in a row. It stays in the playlist, because free streams often drop out for a while.
- **dead**: everything else.

History lives in `state/history.json`, so run `kia check` on a schedule (daily is plenty) and the playlist settles over time. Streams with the same `tvg-id` (or the same name, if untagged) are collapsed into one channel: working beats flaky, then higher resolution, then faster response.

Run checks from the machine that will play the streams, or at least from the same network, since some streams are geo-blocked or block certain IP addresses.

### Using it in Kodi

In *IPTV Simple Client → Configure → General*, set the M3U playlist to `output/curated.m3u` (a local path, or a URL once it's served from your network).

## Development

```sh
.venv/bin/pytest
```

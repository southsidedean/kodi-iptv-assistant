# kodi-iptv-assistant

Turn free internet TV into a tidy channel list with a working program guide in Kodi.

## Why this exists

Kodi and free IPTV are a great combination, but getting from a raw playlist to something you'd actually want to watch is a mess:

- **Lots of the channels don't work.** Public playlists list thousands of streams, and on any given day a good share of them are offline, moved, or blocked where you live.
- **The program guide is mostly empty.** Guide data exists for many channels, but it comes from dozens of different sites, and the channel IDs rarely match the ones in your playlist, so Kodi can't connect them.
- **It doesn't stay fixed.** Streams change daily. A list that works this week is half broken next month.

kia (kodi-iptv-assistant) does that cleanup for you, every night:

- **A clean channel list.** It tests every stream in the playlists you pick and keeps only the ones that work, one per channel.
- **A program guide that matches.** It finds guide data for those channels and builds one guide file that lines up with the list.
- **One address for every device.** It serves both on your network, so each Kodi device is set up once and stays current.

Channels with no guide data anywhere (small local stations, for example) show their name in the guide instead of a blank row.

## What you need

- A computer that stays on, or the one you watch on: Linux, macOS, Windows, or a Raspberry Pi 4/5.
- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on macOS and Windows).
- [Kodi](https://kodi.tv/download/) on the devices you watch on (setup below).

## Quick start

1. Download and configure:

   ```sh
   git clone https://github.com/southsidedean/kodi-iptv-assistant.git
   cd kodi-iptv-assistant
   cp .env.example .env
   ```

   Open `.env` and set `KIA_PUBLIC_URL` to this computer's address (e.g. `http://192.168.1.20:8780`) and `TZ` to your time zone.

2. Pick your playlists: open `sources.txt` and remove the `#` in front of the ones you want. The USA list is on by default.

3. Start it:

   ```sh
   docker compose up -d --build
   ```

   The first run takes up to an hour. Watch it with `docker compose logs -f kia`.

4. Set up Kodi on each device you watch on:
   - **Install Kodi** from [kodi.tv/download](https://kodi.tv/download/) (Windows, macOS, Linux, Android; app stores on some devices).
   - **Add IPTV Simple Client:** *Settings → Add-ons → Install from repository → PVR clients → PVR IPTV Simple Client*.
   - **Point it at kia:** in the add-on's settings, set **Location** to *Remote path* and **M3U play list URL** to `http://<your address>:8780/curated.m3u`. Restart Kodi.

   Channels appear under **TV**, and the guide fills in by itself. Step by step, with tips for Fire TV, Raspberry Pi, and troubleshooting: [docs/kodi.md](docs/kodi.md).

To see what it found, open `http://<your address>:8780/` in a browser: there are reports on working streams and guide coverage.

## Setups

- **Home server:** one machine serves every Kodi device in the house. This is the default.
- **Same machine as Kodi:** a desktop or Linux box that both runs this and plays TV.
- **Laptop that travels:** tests streams wherever you are, copies the guide from your home server when it can reach it, and builds its own when it can't.
- **Raspberry Pi:** works with lower settings.

Recipes for each: [docs/setups.md](docs/setups.md).

## Configuration

| File | For |
|---|---|
| `sources.txt` | Which playlists to use |
| `.env` | How it runs on this machine: address, schedule, time zone, memory |
| `kia.toml` | Fine-tuning: timeouts, filters, guide sources, alerts |

Every setting: [docs/configuration.md](docs/configuration.md).

## How it works

Each night it tests every stream with `ffprobe`, keeps the working ones, matches the channels to guide sources from [iptv-org/epg](https://github.com/iptv-org/epg), downloads the guide data, and keeps the best source per channel. It also checks DNS problems against public resolvers and can alert you when something needs attention. Details: [docs/how-it-works.md](docs/how-it-works.md).

## Credits

Playlists come from [iptv-org/iptv](https://github.com/iptv-org/iptv) and others; guide sources from [iptv-org/epg](https://github.com/iptv-org/epg) and [i.mjh.nz](https://github.com/matthuisman/i.mjh.nz). This project only tests and organizes publicly listed streams; it doesn't host any.

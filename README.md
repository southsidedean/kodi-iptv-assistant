<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/kia-logo-dark.svg">
  <img src="images/kia-logo.svg" alt="kia logo: a screen showing program guide rows" width="88">
</picture>

# kodi-iptv-assistant

Turn free internet TV into a tidy channel list with a working program guide in Kodi and other players.

**New here?** [Start here](docs/start-here.md) explains what you'll end up with and the three steps to get there. Every guide is listed in the [docs index](docs/README.md).

![Kodi's TV guide filled in from kia](images/kodi-iptv-guide.jpg)

## Why this exists

Kodi and free IPTV make a great pair, but turning a raw playlist into something you'd want to watch is a mess. Public playlists list thousands of streams, and on any given day a good share of them are offline, moved, or blocked where you live. Guide data exists for many of those channels, but it's spread across dozens of sites whose channel IDs rarely match your playlist's, so Kodi can't connect them and the guide stays mostly empty. And streams change daily, so a list that works this week is half broken next month.

kia (kodi-iptv-assistant) does the cleanup every night. It tests every stream in the playlists you pick and keeps one working stream per channel. It finds guide data for those channels and builds a single guide file that lines up with the list. Then it serves both on your network, so you set up each device once and it stays current.

Channels with no guide data anywhere (small local stations, for example) show their name in the guide instead of a blank row. kia can also add internet radio stations, which appear under Radio in Kodi and most other players.

## What you need

- A computer to run kia: macOS, Linux, or a Raspberry Pi 4/5. It can be the one you watch on or one that stays on for the house.
- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on macOS) and Git.
- A player on the devices you watch on. [Kodi](https://kodi.tv/download/) is the usual choice, on Windows, macOS, Linux, Android TV, Fire TV, and others (setup below). Plex, Jellyfin, Emby, TiviMate, VLC, and phone apps work too: see [players](#players).

## Quick start

New to Docker or Git? [docs/install.md](docs/install.md) walks through installing everything, step by step. With Docker and Git installed, one command does the rest on macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/southsidedean/kodi-iptv-assistant/main/scripts/install.sh | sh
```

Or by hand:

1. Download and configure:

   ```sh
   git clone https://github.com/southsidedean/kodi-iptv-assistant.git
   cd kodi-iptv-assistant
   cp .env.example .env
   ```

   Open `.env` and set `KIA_PUBLIC_URL` to this computer's address (e.g. `http://192.168.1.20:8780`; [how to find it](docs/install.md#4-find-this-computers-address)) and `TZ` to your time zone.

2. Optional: pick your playlists. The USA list is on by default, which is enough to start. Once kia is running, you can choose others on its status page ([how](docs/configuration.md#choosing-playlists-on-the-status-page)). To choose them now instead, copy the list with `cp sources.txt sources.local.txt`, set `KIA_SOURCES_FILE=sources.local.txt` in `.env`, then remove the `#` in front of the playlists you want in `sources.local.txt`. Updating kia never touches your copy.

3. Start it:

   ```sh
   docker compose up -d --build
   ```

   The first run takes up to an hour. Watch it with `docker compose logs -f kia`.

4. Set up a player on each device you watch on. For Kodi:
   - Install Kodi from [kodi.tv/download](https://kodi.tv/download/) (Windows, macOS, Linux, Android; app stores on some devices).
   - Add IPTV Simple Client: on the home screen choose *Add-ons*, then *Enter add-on browser* (or the box icon at the top left) → *Install from repository → PVR clients → IPTV Simple Client*.
   - In the add-on's settings, set **Location** to *Remote path (Internet address)* and **M3U playlist URL** to `http://<your address>:8780/curated.m3u`. Set **Auto refresh mode** to *Repeated refresh* and **Refresh interval** to 120, so Kodi picks up kia's updates. Then restart Kodi.

   Channels appear under TV, and the guide fills in by itself. Step by step, with tips for Fire TV and Raspberry Pi: [docs/kodi.md](docs/kodi.md).

   Using something else? See [players](#players) below.

To check on kia, open `http://<your address>:8780/` in a browser. The [status page](docs/how-it-works.md#the-status-page) there shows when it last refreshed, whether that worked, how many channels it found, and any alerts. It lists your playlists with their addresses, and links to the detailed reports. To make playlists for people and devices, or to connect other apps, use the [secure status page](docs/secure-page.md) and the [API](docs/api.md). If something isn't working, [docs/troubleshooting.md](docs/troubleshooting.md) goes through the usual problems.

## Players

kia works with any app that opens an M3U playlist, and most of them show its guide too. Each guide below gives the setting names step by step:

| Device | App | Setup |
|---|---|---|
| TV box, Fire TV, Android TV, Raspberry Pi, computer | Kodi | [docs/kodi.md](docs/kodi.md) |
| Android TV, Google TV, Nvidia Shield | TiviMate | [docs/tivimate.md](docs/tivimate.md) |
| Any TV or phone with Plex | Plex, through Threadfin | [docs/plex.md](docs/plex.md) |
| Any TV or phone with Jellyfin or Emby | Jellyfin or Emby | [docs/jellyfin.md](docs/jellyfin.md) |
| iPhone, iPad, Apple TV, Android phone or tablet | UHF, Televizo | [docs/phones.md](docs/phones.md) |
| Computer, phone, or tablet, for a quick look (no guide) | VLC | [docs/vlc.md](docs/vlc.md) |

Costs, which apps show the guide, and watching away from home are covered in [choosing a player](docs/players.md).

## Setups

By default, one machine serves every TV, phone, and computer in the house. kia can also run on the same computer you watch on, on a Raspberry Pi with lower settings, or on a laptop that travels: the laptop tests streams wherever you are, copies the guide from your home server when it can reach it, and builds its own when it can't. Recipes for each are in [docs/setups.md](docs/setups.md). Different people and devices can also get their own, smaller playlists, such as one for the kids: see [docs/profiles.md](docs/profiles.md).

## Configuration

| File | For |
|---|---|
| `sources.txt` | Which playlists and radio stations to use |
| `.env` | How it runs on this machine: address, schedule, time zone, memory |
| `kia.toml` | Fine-tuning: timeouts, filters, guide sources, alerts |

All the settings are listed in [docs/configuration.md](docs/configuration.md).

## How it works

Each night kia tests every stream with `ffprobe`, matches the working channels to guide sources from [iptv-org/epg](https://github.com/iptv-org/epg), downloads the guide data, and keeps the best source for each channel. When a name lookup fails, it checks public DNS to tell a dead stream from a problem on your network. The details are in [docs/how-it-works.md](docs/how-it-works.md).

If something breaks that needs you, such as your network's DNS failing or the nightly refresh crashing, kia can send a notification to your phone or by email. Setup takes about five minutes: [docs/alerts.md](docs/alerts.md).

## Updating and stopping

kia refreshes everything every night at 4:00, and the guide every 4 hours in between. To change those, set `KIA_RUN_AT` and `KIA_GUIDE_REFRESH_HOURS` in `.env`. Changes to your playlists or settings show up after the next run; to refresh right away, see [when changes take effect](docs/configuration.md#when-changes-take-effect).

To update kia, first read the *Upgrading* notes for the new version in the [changelog](CHANGELOG.md); some versions need an extra step. Then run these in the kia folder:

```sh
git pull
docker compose build --pull
docker compose pull --ignore-buildable
docker compose up -d
```

This also brings the latest Node.js and nginx images; to stay on fixed versions, see [versions](docs/configuration.md#versions). If you use kia's [published image](docs/configuration.md#the-published-image) instead of building it, run `git pull`, `docker compose pull`, and `docker compose up -d`. Your `.env`, your own playlist list, and the stream history are kept. If you edited `sources.txt` or `kia.toml` directly, git may ask you to commit or stash those changes first.

To save your settings, playlists, and choices in one file, or to move kia to another computer, see [backing up](docs/configuration.md#backing-up-and-moving-to-another-computer).

To stop kia, run `docker compose down`. While it's stopped, your players can't refresh the channel list or guide.

## Credits

Most of what kia does depends on the [iptv-org](https://github.com/iptv-org) community. Its volunteers track thousands of free channels around the world:

- [iptv-org/iptv](https://github.com/iptv-org/iptv) maintains the public playlists kia checks by default.
- [iptv-org/epg](https://github.com/iptv-org/epg) provides the guide downloaders for hundreds of sites, and the table that links channels to their guides. kia runs it every night.

Other playlists come from [apsattv.com](https://www.apsattv.com/), [Free-TV](https://github.com/Free-TV/IPTV), and [junguler's radio lists](https://github.com/junguler/m3u-radio-music-playlists); radio stations from [radio-browser.info](https://www.radio-browser.info/); and ready-made guides from [i.mjh.nz](https://github.com/matthuisman/i.mjh.nz). Thank you to all of them.

This project only tests and organizes publicly listed streams; it doesn't host any.

## Contributing

Bug reports, ideas, and fixes are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately, as [SECURITY.md](SECURITY.md) describes.

## License

kia is free software under the [GNU Affero General Public License v3.0](LICENSE). You can use, change, and share it. If you share a modified version, or run one as a service for other people, you must make your source code available under the same license.

If you publish a fork, please give it a different name so people don't confuse it with this project.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/kia-logo-dark.svg">
  <img src="images/kia-logo.svg" alt="kia logo: a screen showing program guide rows" width="88">
</picture>

# kodi-iptv-assistant

Free internet TV with a channel list that works and a guide that matches, for Kodi, Plex, Jellyfin, and other players, from one computer at home.

**New here?** [Start here](docs/start-here.md) explains what you'll end up with and the three steps to get there. Every guide is listed in the [docs index](docs/README.md).

![Kodi's TV guide filled in from kia](images/kodi-iptv-guide.jpg)

## Why this exists

Free TV channels streamed over the internet (IPTV) work well in Kodi and other players, but a raw playlist of them is hard to watch. Public playlists list thousands of streams, and on any given day a good share of them are offline, moved, or blocked where you live. Guide data exists for many of those channels, but it's spread across dozens of sites whose channel IDs rarely match your playlist's, so players can't connect them and the guide stays mostly empty. And streams change daily, so a list that works this week is half broken next month.

kia (kodi-iptv-assistant) does the cleanup every night. It tests every stream in the channel sources you pick and keeps one working stream per channel. It finds guide data for those channels and builds a single guide that lines up with the list. Then it serves both on your network, so you set up each device once and it stays current.

## What you get

- A channel list and guide that stay current. kia checks every stream each night and refreshes the guide every 4 hours. A channel with no guide data anywhere shows its name instead of a blank row, and internet radio stations can sit alongside the TV channels.
- Every player in the house can use them. Kodi reads kia's playlist directly, Jellyfin reads it as it is, and Plex gets the channels as Live TV through Threadfin, a TV tuner kia sets up for it. TiviMate, VLC, and phone apps work too.
- A Dashboard in your browser says whether everything's working, lists your playlists with Copy buttons, and shows what's on now and next. With Docker, it also has a tile for each program running next to kia. On the secure Dashboard, after a password, you can make smaller playlists for people and devices (such as the kids), pick favorites and channel numbers, choose channel sources, and test a channel.
- Pluto TV channels keep playing through their ad breaks, where most players freeze or jump back. kia plays them through a relay of its own ([how](docs/how-it-works.md#pluto-tv)).
- kia can message your phone or email you when something breaks ([alerts](docs/alerts.md)), and Grafana can graph how it's all been running ([graphs for your TV stack](docs/cookbooks/observability.md)).
- When something's wrong, kia says why: **Ask kia what's wrong** on the Dashboard, **Why?** next to any channel, or `kia doctor` in a terminal. `kia find` searches the guides for a show, and an AI assistant such as Claude Code can ask all of it in your own words through kia's MCP server ([asking kia for help](docs/ask-kia.md)).

## What you need

- A computer to run kia: macOS, Linux, or a Raspberry Pi 4/5, with 4 GB of memory or more. It can be the one you watch on, or one that stays on for the house.
- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on macOS) and Git.
- A player on each device you watch on. [Kodi](https://kodi.tv/download/) is the usual choice, on Windows, macOS, Linux, Android TV, Fire TV, and others. For the rest, see [players](#players).

## Quick start

New to Docker or Git? [docs/install.md](docs/install.md) walks through installing everything, step by step. With Docker and Git installed, one command does the rest on macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/southsidedean/kodi-iptv-assistant/main/scripts/install.sh | sh
```

It asks for this computer's address and time zone, and what this kia is for, which is one of the [presets](docs/stack.md#presets):

| Preset | For |
|---|---|
| `kodi` (the default) | Kodi and other players that open a playlist |
| `plex-box` | A home TV box: Plex and Threadfin run next to kia ([cookbook](docs/cookbooks/plex-box.md)) |
| `plex-elsewhere` | Threadfin, for a Plex you already run on another computer ([Plex](docs/plex.md)) |
| `jellyfin-box` | A home TV box with Jellyfin ([cookbook](docs/cookbooks/jellyfin-box.md)) |
| `plex-box-graphs`, `jellyfin-box-graphs` | The same TV boxes, with Prometheus and Grafana for graphs |
| `everything` | Plex and Jellyfin side by side, with Prometheus and Grafana |

It also offers the kia-stack service, so you can change what runs later on the Dashboard. At the end it prints the Dashboard's address and the password for changes. kia's first refresh then takes up to an hour.

<details>
<summary>Or install by hand</summary>

1. Download and configure:

   ```sh
   git clone https://github.com/southsidedean/kodi-iptv-assistant.git
   cd kodi-iptv-assistant
   scripts/update.sh --no-restart
   cp .env.example .env
   chmod 600 .env
   ```

   `scripts/update.sh --no-restart` moves the copy to kia's newest release, rather than what's been merged since. Open `.env` and set `KIA_PUBLIC_URL` to this computer's address (such as `http://192.168.1.20:8780`; [how to find it](docs/install.md#4-find-this-computers-address)) and `TZ` to your time zone. On Linux, also set `PUID` and `PGID` to the numbers `id -u` and `id -g` print, so you own the files kia makes (many computers use 1000 for both, the default).

2. Optional: pick your channel sources, the playlists kia reads channels from. The USA list is on by default, which is enough to start, and you can choose others on the Dashboard once kia is running ([how](docs/configuration.md#choosing-channel-sources-on-the-dashboard)). To choose them now instead, copy the list with `cp sources.txt sources.local.txt`, set `KIA_SOURCES_FILE=sources.local.txt` in `.env`, then remove the `#` in front of the sources you want in `sources.local.txt`. Updating kia never touches your copy.

3. Start it:

   ```sh
   docker compose up -d --build
   ```

   The first run takes up to an hour. Watch it with `docker compose logs -f kia`.

</details>

Then set up a player on each device you watch on. For Kodi:

- Install Kodi from [kodi.tv/download](https://kodi.tv/download/) (Windows, macOS, Linux, Android; app stores on some devices).
- Add IPTV Simple Client: on the home screen choose *Add-ons*, then *Enter add-on browser* (or the box icon at the top left) → *Install from repository → PVR clients → IPTV Simple Client*.
- In the add-on's settings, set **Location** to *Remote path (Internet address)* and **M3U playlist URL** to `http://<your address>:8780/curated.m3u`. Set **Auto refresh mode** to *Repeated refresh* and **Refresh interval** to 120, so Kodi picks up kia's updates. Then restart Kodi.

Channels appear under TV, and the guide fills in by itself. Step by step, with tips for Fire TV and Raspberry Pi: [docs/kodi.md](docs/kodi.md).

To check on kia, open `http://<your address>:8780/` in a browser. If something isn't working, [troubleshooting](docs/troubleshooting.md) goes through the usual problems.

## Cookbooks

Each [cookbook](docs/cookbooks/README.md) takes one goal from start to finish:

- [A home TV box with Plex](docs/cookbooks/plex-box.md): one computer runs kia, Threadfin, and Plex, and Live TV appears in every Plex app, next to your own movies and shows.
- [A home TV box with Jellyfin](docs/cookbooks/jellyfin-box.md): the same with Jellyfin, which is free software, with no tuner in between and no channel limit.
- [Kodi and kia on one computer](docs/cookbooks/kodi-computer.md): a laptop or desktop that finds the channels and plays them, with nothing else to set up.
- [A TV in every room](docs/cookbooks/every-room.md): one server for the house, a player on every TV and phone, and a playlist for each room or person.
- [A laptop that travels](docs/cookbooks/travel-laptop.md): it checks the streams where you are and copies the guide from your home server.
- [Graphs for your TV stack](docs/cookbooks/observability.md): Prometheus and Grafana, with dashboards for kia, the containers, Plex, Jellyfin, and the computer's hardware.

## Choosing what runs

The Dashboard's **Stack** page shows a card for each common setup, such as *Plex TV box* or *Jellyfin TV box with graphs*, and lets you pick parts one by one. **Check** tests the choice for this computer, and **Apply** starts it. It asks for Plex's claim code, offers to make Plex's playlist, and says what each new part needs next. [Your stack from the Dashboard](docs/stack-dashboard.md) walks through it the first time.

Underneath, one line in `.env` says which programs run next to kia, such as `KIA_STACK=plex,observability`, and `scripts/stack.sh` checks the choice and starts it. You can use it in a terminal too:

```sh
scripts/stack.sh check     # names it doesn't know, parts that don't go together, ports in use, memory
scripts/stack.sh apply     # start what's chosen and stop the rest, asking first if someone's watching
scripts/stack.sh           # what runs now, and which graphs have data
```

The parts are `plex`, `plex-tuner`, `plex-tuner-2`, `jellyfin`, `observability`, `observability-gpu`, and `observability-disks`. [Choosing the parts](docs/stack.md) explains each one, and [the stack under the hood](docs/stack-under-the-hood.md) shows how it all works.

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

Costs, which apps show the guide, and watching away from home are covered in [choosing a player](docs/players.md). To make a smaller playlist for a person or device, see [playlists for people and devices](docs/profiles.md). For other ways to run kia, such as on a Raspberry Pi or without Docker, see [setups](docs/setups.md).

## Configuration

| File | For |
|---|---|
| `.env` | How it runs on this computer: its address, time zone, schedule, memory, and which programs run next to it (`KIA_STACK`) |
| `sources.txt` | Which channel sources and radio stations to use (or your own copy, `sources.local.txt`) |
| `kia.toml` | Fine tuning: timeouts, filters, guide sources, alerts |

Most everyday choices (channel sources, playlists, favorites, channel numbers) are made on the secure Dashboard instead. All the settings are listed in [docs/configuration.md](docs/configuration.md).

## How it works

Each night kia tests every stream with `ffprobe`, matches the working channels to guide sources from [iptv-org/epg](https://github.com/iptv-org/epg), downloads the guide data, and keeps the best source for each channel. When a name lookup fails, it checks public DNS to tell a dead stream from a problem on your network. The details, and the files kia publishes for scripts (`status.json` and others), are in [how it works](docs/how-it-works.md). kia also publishes its numbers for Prometheus at `/metrics` ([metrics](docs/metrics.md)), and has an [API](docs/api.md) for other apps.

## Security

kia is meant for your home network, and each part of it gets only what it needs:

- kia's own containers run with a read-only system and no extra privileges. kia can't reach Docker, and writes only its own folders. Some optional programs need more, such as the disk health exporter, which reads the disks directly.
- The kia-stack service, which lets the Dashboard change what runs, takes only a check or an apply of parts it knows, and treats each request as coming from a stranger.
- iptv-org's guide downloader, outside code that changes daily, runs in Node.js's sandbox and sees none of kia's passwords or tokens.
- Changes on the Dashboard need a password and are accepted only over HTTPS.
- The MCP server for AI assistants is off until you turn it on. Over the network it answers only over HTTPS with its own token; `kia mcp`, for an assistant on the same computer, needs neither. Either way it can only read.
- The published images are scanned before they go out, and come with a signed record of how they were built.

[SECURITY.md](SECURITY.md) has the details, including what each optional program can do. Don't open a port for kia on your router; to watch away from home, use a VPN such as Tailscale ([away from home](docs/players.md#away-from-home)).

## Updating and stopping

kia refreshes everything every night at 4:00, and the guide every 4 hours in between. To change those, set `KIA_RUN_AT` and `KIA_GUIDE_REFRESH_HOURS` in `.env`. Changes to your playlists or settings show up after the next run; to refresh right away, see [when changes take effect](docs/configuration.md#when-changes-take-effect).

To update kia, first read the *Upgrading* notes for the new version in the [changelog](CHANGELOG.md); some versions need an extra step. Then, in the kia folder:

```sh
scripts/update.sh
```

It moves kia to its newest release, rebuilds it, and restarts it. If someone is watching through Pluto TV, Plex, or Jellyfin, it asks first, since a restart cuts them off. It also brings the latest images kia uses (its Wolfi base, nginx, and Plex, Jellyfin, Threadfin, Prometheus, and Grafana if you use them); to stay on fixed versions, see [versions](docs/configuration.md#versions). `scripts/update.sh v0.5.1` goes to a given release, and `--main` follows what's merged since the last one. Your `.env`, your own channel sources, and the stream history are kept. If you edited one of kia's own files (`sources.txt` or `kia.toml`, say), it stops and says so: keep your changes in `sources.local.txt` and `.env` instead.

To save your settings, playlists, and choices in one file, or to move kia to another computer, see [backing up](docs/configuration.md#backing-up-and-moving-to-another-computer).

To stop kia, run `docker compose down`. While it's stopped, your players can't refresh the channel list or guide.

## Credits

Most of what kia does depends on the [iptv-org](https://github.com/iptv-org) community. Its volunteers track thousands of free channels around the world:

- [iptv-org/iptv](https://github.com/iptv-org/iptv) maintains the public playlists kia checks by default.
- [iptv-org/epg](https://github.com/iptv-org/epg) provides the guide downloaders for hundreds of sites, and the table that links channels to their guides. kia runs it every night.

Other playlists come from [apsattv.com](https://www.apsattv.com/), [Free-TV](https://github.com/Free-TV/IPTV), and [junguler's radio lists](https://github.com/junguler/m3u-radio-music-playlists); radio stations from [radio-browser.info](https://www.radio-browser.info/); and ready-made guides from [i.mjh.nz](https://github.com/matthuisman/i.mjh.nz). Plex gets its tuner from [Threadfin](https://github.com/Threadfin/Threadfin). Thank you to all of them.

This project only tests and organizes publicly listed streams; it doesn't host any.

## Contributing

Bug reports, ideas, and fixes are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately, as [SECURITY.md](SECURITY.md) describes.

## License

kia is free software under the [GNU Affero General Public License v3.0](LICENSE). You can use, change, and share it. If you share a modified version, or run one as a service for other people, you must make your source code available under the same license.

If you publish a fork, please give it a different name so people don't confuse it with this project.

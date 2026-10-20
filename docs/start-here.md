# Start here

New to kia, or to internet TV in general? This page explains what you'll end up with and the order to do things in. Each step links to a guide with the details.

## What you'll have

- A channel list of free internet TV channels and radio stations that work, tested again every night.
- A TV guide showing what's on, for as many of those channels as kia can find one for.
- If you want them, smaller playlists for people and devices: a short list for the kids' tablet, another for the living room.
- A status page in your browser that says whether everything's working.

kia runs on one computer at home and shares all of this on your home network. Your TVs, phones, and computers play the channels with an app such as Kodi.

## The three steps

1. Install kia on one computer. A Mac, a Linux computer, or a Raspberry Pi that stays on works best. [Installing kia](install.md) walks through it, including the two programs it needs (Docker and Git). Allow about 30 minutes, plus up to an hour for kia's first run, which you don't need to watch.
2. Set up the devices you watch on. On a TV box, Fire TV, or computer, [Kodi](kodi.md) is the usual choice. For Plex, Jellyfin, TiviMate, phones, and others, see [choosing a player](players.md).
3. Check the status page. Open `http://<your address>:8780/` in a browser. Its **Getting started** checklist shows what's left to do. The page also shows the last refresh, the channel counts, and your playlists' addresses with Copy buttons.

From then on kia updates the channels every night and the guide every few hours, and your devices pick up the changes by themselves.

## Words you'll see

| Word | What it means |
|---|---|
| Playlist, M3U | A list of channels and their internet addresses. Files end in `.m3u`. kia's is `curated.m3u`. |
| Guide, EPG, XMLTV | The TV guide: what's on each channel and when. EPG means "electronic program guide", and XMLTV is its file format. kia's is `guide.xml.gz`. |
| Stream | One channel's video or audio coming over the internet. |
| Source | A playlist kia checks, listed in `sources.txt`, such as iptv-org's list of US channels. |
| Profile | A smaller playlist for one person or device, made from the full list. See [profiles](profiles.md). |
| Your address | How other devices reach the kia computer, such as `192.168.1.20`. [Installing kia](install.md#4-find-this-computers-address) shows how to find it. |

## Where to go next

| You want to | Read |
|---|---|
| Make a playlist for the kids or a room | [Playlists for people and devices](profiles.md) |
| Add channels from other countries, or radio | [Choosing playlists on the status page](configuration.md#choosing-playlists-on-the-status-page), or the comments in `sources.txt` |
| Get a message when something breaks | [Alerts](alerts.md) |
| Watch away from home | [Away from home](players.md#away-from-home) |
| Fix something that isn't working | [Troubleshooting](troubleshooting.md) |
| Understand what kia does each night | [How it works](how-it-works.md) |
| Learn more about Kodi itself | The [Kodi Wiki](https://kodi.wiki/view/Main_Page) |

All the guides are listed in the [docs index](README.md).

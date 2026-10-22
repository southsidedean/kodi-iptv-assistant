# A home TV box: kia, Threadfin, and Plex in containers

One computer runs everything: kia finds and checks free TV channels and builds their guide, Threadfin presents them as a TV tuner, and Plex Media Server shows them as Live TV in every Plex app, next to your own movies and shows. It all runs in Docker, set up from one settings file, and it keeps itself up to date.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../images/diagrams/plex-box-dark.svg">
  <img alt="kia's playlists and guides go to Threadfin, which kia sets up. Threadfin feeds Plex Media Server, where kia adds Live TV. Your media folder is read-only to Plex. Plex serves the Plex apps on your TV, phone, and browser." src="../../images/diagrams/plex-box-light.svg">
</picture>

What stays on disk, so nothing is lost when the containers restart or update:

| Folder (in the kia folder) | What's in it | Set in `.env` |
|---|---|---|
| `plex/config` | Plex's settings, database, and artwork | `KIA_PLEX_CONFIG` |
| `plex/transcode` | Plex's temporary files while it converts video | `KIA_PLEX_TRANSCODE` |
| `media` | Your movies, shows, and music; Plex can read them but not change them | `KIA_MEDIA_DIR` |
| `threadfin/`, `state/`, `profiles/` | Threadfin's and kia's settings | (always here) |

If Plex already runs on another computer, or outside Docker, use the [Plex guide](../plex.md) instead: it adds kia to the Plex you have.

## What you need

- A computer that stays on, running Linux with Docker. A small PC or an old laptop is enough for Live TV; converting video for your own media takes more.
- A free [Plex account](https://www.plex.tv/sign-up/). Watching Live TV is free; recording needs [Plex Pass](https://www.plex.tv/plex-pass/).
- About 3 GB of disk for kia and Plex, plus room for your media.

## 1. Install kia

Follow [installing kia](../install.md), steps 1 to 6. The quick way is one command, which also asks for this computer's address; this guide uses `192.168.1.20` as the example. Wait for the first refresh to finish: the Dashboard at `http://192.168.1.20:8780/` says when it's done.

## 2. Make the playlist for Plex

Plex takes at most 480 channels from each tuner, so make a playlist of the channels you want in Plex. On the secure Dashboard, `https://192.168.1.20:8743/`, go to **Playlists** and choose **Make a playlist**:

1. Name it `plex`.
2. Choose **Only the channels I pick**, and tick the categories you watch.
3. Under **Plex**, tick **For Plex through Threadfin**.
4. Save, and check the count is 480 or fewer.

For more than 480 channels, make two playlists, `plex-1` and `plex-2`; the [Plex guide](../plex.md#1-make-a-playlist-for-plex) shows how to split them.

## 3. Get a claim code from Plex

A claim code links the new Plex to your account the first time it starts. Sign in at [plex.tv/claim](https://plex.tv/claim) and copy the code, which starts with `claim-`. It works for 4 minutes, so do the next step straight away.

## 4. Turn on Plex

In the kia folder, set these lines in `.env` (they're already in the file, empty; fill those in, because Docker Compose uses the last copy of a line):

```sh
COMPOSE_PROFILES=plex,plex-server
PLEX_CLAIM=claim-xxxxxxxxxxxxxxxxxxxx
KIA_PLEX_ADVERTISE=http://192.168.1.20:32400/
KIA_PLEX_URL=http://plex:32400
```

For two tuners, use `COMPOSE_PROFILES=plex,plex-2,plex-server`. To use your own media folder (a second disk, say), set `KIA_MEDIA_DIR=/path/to/your/media`. The server is called `kia-tv-box` in Plex apps; `KIA_PLEX_NAME` changes that.

Then start everything:

```sh
docker compose up -d
docker compose ps
```

kia sets up Threadfin before it starts, with your playlist, its guide, and the settings that work (see the [Plex guide](../plex.md) for what they are). After a minute or two, `docker compose ps` shows `kia`, `web`, `threadfin`, and `plex`, each `healthy` or `running`.

## 5. Add Live TV to Plex

```sh
docker compose exec kia python3 -m kia plex-tuners
```

```text
Plex hasn't downloaded its codecs yet, which Live TV needs; playing a short clip so it does.
Plex has its codecs now.
Made a new DVR in Plex for kia's Threadfins (DVR 2).
Plex: 442 channels, guide http://threadfin:34400/xmltv/threadfin.xml
Plex is loading the guide now; the channels show under Live TV in a few minutes.
```

kia uses Plex's own sign-in, so there's no token to find. A new Plex downloads its codecs (the parts that read video and sound) only when it first plays a video, and Live TV can't play without them, so the first time, kia plays a 10-second test clip through Plex and then removes it. From now on, kia brings Plex up to date after each refresh, so new channels arrive in Plex by themselves.

## 6. Watch

Open Plex at `http://192.168.1.20:32400/web`, or any Plex app signed in to your account. The first time, Plex shows a short welcome and asks for the server's name and your libraries; you can skip the libraries for now. Then **Live TV** has your channels and their guide, under `kia-tv-box`.

![Plex's Live TV guide on the kia-tv-box server: channel logos down the side and each channel's shows over the next two hours](../../images/cookbooks/plex-box-guide.png)

To add your own movies and shows, put them in the media folder, organized the way Plex likes ([movies](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/)), and [make a library](https://support.plex.tv/articles/200288926-creating-libraries/) in Plex. In Plex, the media folder is `/data`.

## Keeping it running

- Everything starts again when the computer restarts.
- **Updating kia:** follow the steps in the [README](../../README.md#updating-and-stopping), which also update the images, Plex's included. Do it when nobody's watching, because Plex and Threadfin restart.
- **Updating Plex:** `docker compose pull plex`, then `docker compose up -d`.
- **Backups:** `docker compose exec kia python3 -m kia backup` saves kia's and Threadfin's settings ([backups](../configuration.md#backing-up-and-moving-to-another-computer)). Plex's own settings in `plex/config` can be large; Plex explains [what to back up](https://support.plex.tv/articles/201539237-backing-up-plex-media-server-data/).

## If something's wrong

**Plex isn't on your account.** The claim code had expired. Get a new one, put it in `PLEX_CLAIM`, and run `docker compose up -d --force-recreate plex`.

**`plex-tuners` says Plex hasn't been claimed yet.** Same as above.

**Plex apps can't connect, or play only at low quality.** Check that `KIA_PLEX_ADVERTISE` has this computer's address and Plex's port, then run `docker compose up -d`.

**Threadfin has no channels.** It started before the playlist existed. Make the playlist (step 2), then run `docker compose restart threadfin`.

For the rest, see the [Plex guide](../plex.md#if-somethings-wrong) and [troubleshooting](../troubleshooting.md).

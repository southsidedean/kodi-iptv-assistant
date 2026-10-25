# Plex

Plex can't open a playlist by itself. It does live TV through tuners such as the HDHomeRun, so kia runs a small free program next to it, [Threadfin](https://github.com/Threadfin/Threadfin), that shows kia's playlist and guide to Plex as an HDHomeRun tuner. Threadfin starts and stops with kia, keeps its settings in the kia folder, and is included in [kia's backups](configuration.md#backing-up-and-moving-to-another-computer). These steps were tested with Threadfin 1.2.37 and Plex Media Server 1.43.

Don't have Plex yet? The [home TV box cookbook](cookbooks/plex-box.md) runs Plex, Threadfin, and kia together on one computer, set up for you.

Plex says watching through a tuner is free, and recording (DVR) needs [Plex Pass](https://www.plex.tv/plex-pass/). Plex changes its plans from time to time, so check its [Live TV & DVR](https://support.plex.tv/articles/225877347-live-tv-dvr/) page.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/plex-one-tuner-dark.svg">
  <img alt="kia's plex.m3u and its guide go to Threadfin on port 34400, which looks like an HDHomeRun tuner to Plex Media Server, which serves the Plex apps on your TV, phone, and browser." src="../images/diagrams/plex-one-tuner-light.svg">
</picture>

## Before you start

- kia running with Docker Compose ([install](install.md)).
- Plex Media Server on a computer in the same home network. It can be the kia computer or another one, installed any way you like (a package, an app, or a container).
- An idea of how many channels you want in Plex. **Plex takes at most 480 channels from each tuner.** Up to 480, you need one Threadfin. For more, run two, each with its own playlist; Plex shows both tuners' channels together in one guide.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/plex-two-tuners-dark.svg">
  <img alt="kia feeds Threadfin on port 34400 with plex-1.m3u and Threadfin 2 on port 34401 with plex-2.m3u, up to 480 channels each. Plex Media Server shows both in one guide." src="../images/diagrams/plex-two-tuners-light.svg">
</picture>

## 1. Make a playlist for Plex

A shorter list is quicker for Plex to load and easier to browse. On kia's secure page ([how to open it](secure-page.md)), choose **Make a playlist**:

1. Name it `plex`.
2. Choose **Only the channels I pick** and tick the categories you watch, such as News, Movies, and Sports.
3. Under **Plex**, tick **For Plex through Threadfin**. Threadfin misreads channel names that have an apostrophe, such as "Hogan's Heroes", so this writes them with a curly one (’) instead.
4. Save, and check the channel count is 480 or fewer.

For two tuners, make two playlists named `plex-1` and `plex-2`, each with **For Plex through Threadfin** ticked and 480 channels or fewer. Split the categories between them. A channel can belong to more than one category, so in `plex-2`, also leave out the categories you picked for `plex-1`; that keeps each channel on one tuner. In a [profiles file](profiles.md), `exclude_channels` does the same for single channels.

## 2. Start Threadfin with kia

In kia's folder, set this line in the `.env` file (it's already there, empty):

```ini
KIA_STACK=plex-tuner
```

For two tuners, write `KIA_STACK=plex-tuner,plex-tuner-2` instead. Add any other parts you use, such as `observability` ([choosing the parts](stack.md)). Don't use `plex` here: that part runs Plex itself too, for a Plex in kia's stack. Then check the choice and start it:

```sh
scripts/stack.sh apply
docker compose ps
```

`docker compose ps` lists `threadfin` (and `threadfin-2`) next to kia, and after a minute each says `healthy`. From now on Threadfin starts whenever kia does, including after the computer restarts. Its settings are kept in the `threadfin/` folder (and `threadfin-2/`).

Threadfin's page is at `http://<your address>:34400/web/`, and the second one's is at port 34401. To use other ports, set `KIA_THREADFIN_PORT` and `KIA_THREADFIN_2_PORT` in `.env` ([settings](configuration.md)). It asks for a login: the user is `admin`, and the password is in `threadfin/conf/admin-password.txt` in the kia folder (`threadfin-2/conf/` for the second). kia makes them the first time, so no one else on your network can change Threadfin's settings; Plex's own access doesn't need them.

## 3. Check Threadfin

kia sets Threadfin up the first time it starts: it reads your `plex` playlist (or `plex-1`, and `plex-2` for a second Threadfin) and its guide, passes on every channel, uses kia's guide, and has 4 tuners and FFmpeg options that pass on one stream. To read a different playlist, set `KIA_THREADFIN_PLAYLIST` in `.env` before the first start. kia never changes Threadfin's settings after that, so changes you make on Threadfin's own page stay.

Open `http://<your address>:34400/web/` and choose **Mapping**: every channel is listed with kia's channel number and its guide.

![Threadfin's Mapping page listing channels with their numbers, names, categories, and guide](../images/plex/threadfin-mapping.png)

If you made the playlist after Threadfin first started, run `docker compose restart threadfin` so it reads it.

### Setting it up by hand

For a Threadfin set up before kia did this, or to see what kia sets, these are the steps. Do them in this order: Threadfin matches each channel to the guide when it first sees the channel, so the guide has to be in place before step 4.

**1. Playlist → New.** Choose **M3U** and **Next**, then fill in:

| Field | What to enter |
|---|---|
| Name | `kia plex` |
| M3U File | `http://web/plex.m3u` |
| Buffer | **FFmpeg** |
| Tuner / Streams | `4` (how many channels can play at once) |

`web` is kia's own name on Docker's network, so this address keeps working even if the computer's address changes. Choose **Save**.

![Threadfin's new playlist form with the name, kia's playlist address, the FFmpeg buffer, and 4 streams filled in](../images/plex/threadfin-playlist.png)

**2. XMLTV → New.** Name it `kia plex guide`, enter `http://web/plex.guide.xml.gz` as the XMLTV file, and choose **Save**. Threadfin takes a few seconds to read the guide.

**3. Filter → New.** Threadfin only passes on channels that a filter picks. Choose **Custom Filter** and **Next**, name it `Everything`, type a single space in **Filter** (it matches every channel kia writes), and choose **Save**.

![Threadfin's filter list with one custom filter named Everything](../images/plex/threadfin-filter.png)

**4. Settings.** Change three settings, then choose **Save** at the top:

| Setting | Change it to |
|---|---|
| Number of Tuners | `4`, the same as the playlist's streams |
| EPG Source | **XEPG** (use the guide from step 2) |
| FFmpeg Options | `-hide_banner -loglevel error -i [URL] -c copy -f mpegts pipe:1` |

Threadfin's own FFmpeg options copy every quality of a stream at once, which uses about four times the bandwidth and can leave Plex showing the lowest quality. The options above pass on the best one.

**5. Mapping.** Every channel is now listed with kia's channel number and its guide.

For a second tuner, do the same at `http://<your address>:34401/web/` with `http://web/plex-2.m3u` and `http://web/plex-2.guide.xml.gz`, and use `plex-1` in place of `plex` in the first Threadfin.

## 4. Add Threadfin to Plex

There are two ways. kia can add the tuners for you, which works whether or not Plex already has a DVR (an antenna tuner, say). Or you can add them in Plex's settings, which works only when Plex has no DVR yet: Plex won't add an XMLTV guide to a DVR that uses its own guide.

### Let kia add them

1. Find a Plex token by following Plex's [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). The token works like your Plex password, so keep it to yourself.
2. In kia's `.env`, set Plex's address and the token:

   ```sh
   KIA_PLEX_URL=http://192.168.1.20:32400
   KIA_PLEX_TOKEN=your-token
   ```

3. Run:

   ```sh
   docker compose up -d
   docker compose exec kia python3 -m kia plex-tuners
   ```

   ```text
   Made a new DVR in Plex for kia's Threadfins (DVR 15).
   Plex 1: Pluto and series: 340 channels, guide http://192.168.1.20:34400/xmltv/threadfin.xml
   Plex 2: news, sports, and movies: 271 channels, guide http://192.168.1.20:34401/xmltv/threadfin.xml
   Plex is loading the guide now; the channels show under Live TV in a few minutes.
   ```

kia puts all its Threadfins in one DVR of their own, each with its own guide, names each one after its kia playlist, and turns on every channel. A DVR you already have stays as it is. Running it again is safe: it updates the same DVR, and picks up channels your playlists have gained. With `KIA_PLEX_URL` and `KIA_PLEX_TOKEN` set, kia also does this by itself after each refresh.

Plex's **Settings → Live TV & DVR** page shows only your first DVR. If you already had one, the Threadfin DVR doesn't appear there, but its channels are under **Live TV** in every Plex app. To delete it, run `docker compose exec kia python3 -m kia plex-tuners --remove`; it deletes only a DVR that holds nothing but kia's Threadfins.

### In Plex's settings

When Plex has no DVR yet, go to **Settings → Live TV & DVR** and choose **Set Up Plex Tuner**.

1. Choose **Don't see your HDHomeRun device? Enter its network address manually**, enter `<your address>:34400`, and choose **Connect**. Threadfin appears as a tuner with your channel count.

   ![Plex's Tuner Setup showing Threadfin found as a tuner with 340 channels](../images/plex/plex-tuner-found.png)

2. Choose **Have an XMLTV guide on your server? Click here to use it.** Enter `http://<your address>:34400/xmltv/threadfin.xml` as the XMLTV guide, give it a title such as `Plex 1 guide`, and choose **Continue**.

   ![Plex's XMLTV guide form with Threadfin's guide address and the title Plex 1 guide](../images/plex/plex-xmltv.png)

3. Plex matches every channel to the guide. Choose **Continue**.

   ![Plex's channel list with every tuner channel matched to a guide channel](../images/plex/plex-channels.png)

Plex then downloads the guide, and the channels appear under **Live TV** in every Plex app.

For a second tuner, go back to **Settings → Live TV & DVR**, choose **Add Another Device**, and repeat the three steps with port 34401: connect to `<your address>:34401`, choose **Next**, and enter `http://<your address>:34401/xmltv/threadfin.xml` with a title such as `Plex 2 guide`. Plex keeps both tuners in one guide.

![Plex's Live TV & DVR page with two Threadfin tuners and their guides](../images/plex/plex-two-tuners.png)

Use the kia computer's network address (such as `192.168.1.20`), not `localhost`. If Plex runs in a container on the same computer, `localhost` would mean the Plex container itself, unless that container shares the computer's own network (Docker's `network_mode: host`).

## Keeping it up to date

kia refreshes the playlists every night and the guide every few hours. Threadfin reads them again every night, and Plex refreshes its guide on its own schedule; **Refresh Guide** on Plex's Live TV & DVR page does it right away.

When the playlist gains channels, Threadfin adds them, but Plex keeps the channel list it had at setup. kia adds the new ones after each refresh when `KIA_PLEX_URL` is set; otherwise run `docker compose exec kia python3 -m kia plex-tuners` again, or on Plex's Live TV & DVR page choose the tuner's channel count (such as **340 enabled**), then **Rescan**, then **Save**. To keep a channel out of Plex, leave it out of the playlist on kia's page rather than in Threadfin, so the change survives the next refresh.

To update Threadfin, set `KIA_THREADFIN_IMAGE` in `.env` to the version you want and run `docker compose up -d`.

## If something's wrong

**Plex says "There was a problem adding the device".** Check the address and port, and open `http://<your address>:34400/discover.json` in a browser on another device. If it doesn't open, check that `docker compose ps` shows Threadfin as healthy and that no firewall blocks the port.

**`docker compose ps` shows Threadfin restarting, and its log says "permission denied".** Its folders were made by Docker for another user. In kia's folder, give them back to yourself and start it again:

```sh
sudo chown -R "$(id -u):$(id -g)" threadfin threadfin-2
docker compose up -d
```

**Plex shows no channels.** Look at Threadfin's **Mapping** page. Channels only appear there after a filter picks them (**Filter → New**, step 3 of [setting it up by hand](#setting-it-up-by-hand)).

**Channels have no guide, or Mapping shows `-` for their XMLTV file.** Threadfin saw the channels before it had the guide, and it doesn't match them again later. Have it start over (your playlist, guide, filter, and settings stay):

```sh
docker compose stop threadfin
echo '{}' > threadfin/conf/xepg.json
docker compose start threadfin
```

**Some channels have numbers from 1000 up, or cut-off names.** Their names have an apostrophe. Edit the playlist on kia's page and tick **For Plex through Threadfin**.

**A channel says "There was an unexpected error during playback" in Firefox.** This is a bug in Plex's web app in Firefox (seen with Plex Web 4.163), and it hits some channels and not others. The same channels play in Chrome, Safari, and the Plex apps.

**The guide in Plex is empty.** Choose **Refresh Guide** on Plex's Live TV & DVR page.

For anything else, see [troubleshooting](troubleshooting.md).

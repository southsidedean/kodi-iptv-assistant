# Plex

Plex can't open a playlist by itself. It does live TV through tuners such as the HDHomeRun, so you need a small free program, [Threadfin](https://github.com/Threadfin/Threadfin), that presents kia's playlist and guide to Plex as an HDHomeRun tuner. These steps were tested with Threadfin 1.2.

Plex says watching through a tuner is free, and recording (DVR) needs [Plex Pass](https://www.plex.tv/plex-pass/). Plex changes its plans from time to time, so check its [Live TV & DVR](https://support.plex.tv/articles/225877347-live-tv-dvr/) page.

## 1. Pick a playlist for Plex

A shorter list is quicker for Plex to load and easier to browse. Make a [profile](profiles.md) with the categories you watch, for example on kia's secure status page: **Make a playlist**, name it `plex`, choose **Only the channels I pick**, tick categories such as News, Movies, and Sports, and save. You'll use:

- Playlist: `http://<your address>:8780/plex.m3u`
- Guide: `http://<your address>:8780/plex.guide.xml.gz`

## 2. Run Threadfin

On the kia machine (Threadfin keeps its settings in `~/threadfin`):

```sh
docker run -d --name threadfin --restart unless-stopped -p 34400:34400 \
  -e PUID=$(id -u) -e PGID=$(id -g) -e TZ=America/New_York \
  -v "$HOME/threadfin/conf:/home/threadfin/conf" \
  fyb3roptik/threadfin
```

Open `http://<your address>:34400/web/`.

## 3. Set up Threadfin

1. **Settings:** set **EPG Source** to **XEPG**, set **Tuner** to the number of channels you might watch at once (4 is plenty for most homes), and choose **Save**.
2. **Playlist → New:** choose **M3U**, give it a name such as `kia`, paste the playlist address, and save.
3. **XMLTV → New:** give it a name, paste the guide address, and save.
4. **Filter → New:** choose **M3U: Group Title**, pick a category, and save. Repeat for each category you want. (With a profile made for Plex, add each of its categories.)
5. **Mapping:** the channels from your filters are listed, each already matched to kia's guide and keeping kia's channel number.

## 4. Add it to Plex

1. In Plex, go to **Settings → Live TV & DVR** and choose **Set Up Plex DVR**.
2. If Threadfin isn't found, choose **Don't see your HDHomeRun device?** and enter `<your address>:34400`.
3. Pick the channels to use, and when Plex asks about the guide, choose the option to use an XMLTV guide. Enter `http://<your address>:34400/xmltv/threadfin.xml`.

Channels then appear under **Live TV** in every Plex app.

## If something's wrong

If Plex shows no channels, look at Threadfin's **Mapping** page. Channels only appear there after a **Filter** picks them.

If the channels appear but the guide is empty, refresh the guide in Plex under **Settings → Live TV & DVR**. Threadfin passes along kia's guide, which kia refreshes every few hours.

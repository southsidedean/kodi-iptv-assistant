# Jellyfin and Emby

Jellyfin and Emby can use kia's playlist and guide directly, and then show live TV in their apps on TVs, phones, and browsers. These steps were tested with Jellyfin 12.2. Emby's screens are similar, but Emby needs an [Emby Premiere](https://emby.media/premiere.html) subscription for live TV.

## Before you start: a TV-only playlist

Jellyfin lists kia's radio stations as TV channels. kia numbers radio separately from TV, both starting at 1, so you'd see two channel 1s. Make a [profile](profiles.md) for Jellyfin with radio left out, for example on kia's secure status page: **Add a playlist**, name it `jellyfin`, untick **Radio stations**, and save. Then use its two addresses below:

- Playlist: `http://<your address>:8780/jellyfin.m3u`
- Guide: `http://<your address>:8780/jellyfin.guide.xml.gz`

The full list works too (`curated.m3u` and `guide.xml.gz`), with the radio stations mixed in.

## Jellyfin

1. Open Jellyfin in a browser and go to **Dashboard → Live TV**.
2. Under **Tuner Devices**, choose **Add Tuner Device**. Set **Tuner type** to **M3U Tuner**, paste the playlist address into **File or URL**, and save. Leave the other settings as they are.
3. Under **TV Guide Data Providers**, choose **Add Provider → XMLTV**. Paste the guide address into **File or URL** and save.
4. Choose **Refresh Guide Data**. With the full list, this takes several minutes the first time.

Channels appear under **Live TV** in every Jellyfin app, with kia's channel numbers and guide. Jellyfin matches channels to the guide by kia's channel IDs, so there's nothing to map by hand. It checks the guide for updates by itself; kia refreshes it every few hours.

## Emby

1. Open the server dashboard and go to **Live TV**.
2. Under tuner devices, choose **Add**, pick **M3U Tuner**, and enter the playlist address.
3. Add a guide data provider, choose **Xml TV**, and enter the guide address.

## If something's wrong

If the channels appear but the guide is empty, open the guide address in a browser on the machine running Jellyfin (it should download a file), then choose **Refresh Guide Data** again.

If a channel won't play, try it in another player such as VLC. kia tests every stream nightly, but some stop working during the day or only play in some apps. [Troubleshooting](troubleshooting.md) has more.

# Jellyfin and Emby

Jellyfin and Emby can use kia's playlist and guide directly, and then show live TV in their apps on TVs, phones, and browsers. These steps were tested with Jellyfin 12.3. Emby's screens are similar, but Emby needs an [Emby Premiere](https://emby.media/premiere.html) subscription for live TV.

## Before you start: the TV-only playlist

Jellyfin lists kia's radio stations as TV channels. kia numbers radio separately from TV, both starting at 1, so you'd see two channel 1s. Use kia's TV-only playlist instead, which kia always makes (with no radio in your list, it has all your channels). It's on the Dashboard as **TV only**, with these two addresses:

- Playlist: `http://<your address>:8780/tv.m3u`
- Guide: `http://<your address>:8780/tv.guide.xml.gz`

For a smaller list, make a [profile](profiles.md) with radio left out and the categories you watch, and use its addresses. The full list works too (`curated.m3u` and `guide.xml.gz`), with the radio stations mixed in.

## Jellyfin

1. Open Jellyfin in a browser and go to **Dashboard → Live TV**.
2. Under **Tuner Devices**, choose **Add Tuner Device**. Set **Tuner type** to **M3U Tuner**, paste the playlist address into **File or URL**, and save. Leave the other settings as they are.
3. Under **TV Guide Data Providers**, choose **Add Provider → XMLTV**. Paste the guide address into **File or URL** and save.
4. Choose **Refresh Guide Data**. With the full list, this takes several minutes the first time.

Channels appear under **Live TV** in every Jellyfin app, with kia's channel numbers and guide. Jellyfin matches channels to the guide by kia's channel IDs, so there's nothing to map by hand. Jellyfin reads the guide again every day by itself (its **Refresh Guide** task, under **Dashboard → Scheduled Tasks**), and kia keeps it current. To see kia's changes sooner, run that task more often, or choose **Refresh Guide Data** on the Live TV page.

## Emby

1. Open the server dashboard and go to **Live TV**.
2. Under tuner devices, choose **Add**, pick **M3U Tuner**, and enter the playlist address.
3. Add a guide data provider, choose **Xml TV**, and enter the guide address.

## If something's wrong

**Ask kia first.** With `KIA_JELLYFIN_URL` and `KIA_JELLYFIN_TOKEN` in `.env` (the key comes from Jellyfin's **Dashboard → API Keys**), `docker compose exec kia python3 -m kia doctor jellyfin` asks Jellyfin what its Live TV reads. It says whether that's one of kia's playlists, whether it has radio stations in it, and whether the guide goes with it. It also checks how many channels Jellyfin shows and how its last guide refresh went, with what to do about each. **Ask kia what's wrong** on the Dashboard includes the same problems.

If the channels appear but the guide is empty, open the guide address in a browser on the machine running Jellyfin (it should download a file), then choose **Refresh Guide Data** again.

**A channel's name is missing its first word, a number** ("Hour Free Movies" for *24 Hour Free Movies*). Jellyfin takes a number at the start of a channel name as its channel number. The channel, its number, and its guide are right; only the name is shorter.

If a channel won't play, try it in another player such as VLC. kia tests every stream nightly, but some stop working during the day or only play in some apps. [Troubleshooting](troubleshooting.md) has more.

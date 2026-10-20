# TiviMate

TiviMate is a TV-guide-style player for Android TV, Google TV, and the Nvidia Shield, made for a remote control. It isn't meant for phones; for those, see [phones and tablets](phones.md).

The free version plays one playlist with its guide. TiviMate Premium, bought in the app, adds more playlists, favorites, and recording.

## Set it up

1. Install **TiviMate IPTV Player** from the Play Store on the TV device.
2. Open it and choose **Add playlist**, then **Enter URL**.
3. Type the playlist address: `http://<your address>:8780/curated.m3u`, or a [profile's](profiles.md) playlist such as `http://<your address>:8780/kids.m3u`. Typing with a remote is slow; a profile with a short name helps, and so does the TV's phone remote app if it has one.
4. Choose **Next**. TiviMate loads the channels and reads where the guide is from the playlist.
5. If it asks for an EPG source, or the guide stays empty, add the guide address: `http://<your address>:8780/guide.xml.gz`, or the profile's own, such as `http://<your address>:8780/kids.guide.xml.gz`.

TiviMate shows kia's channel numbers and categories. In its settings, set the playlist and guide to update every few hours; kia refreshes the guide that often.

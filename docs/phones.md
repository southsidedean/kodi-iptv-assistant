# Phones and tablets

Any of these apps plays kia's channels on a phone or tablet at home. On a small screen, a [profile](profiles.md) with just the channels you watch is much easier to scroll than the full list.

| | App | Guide | Cost |
|---|---|---|---|
| iPhone, iPad (and Apple TV) | [UHF](https://apps.apple.com/us/app/uhf-love-your-iptv/id6443751726) | Yes | Free; a PRO plan adds syncing between devices and removes ads |
| Android phone or tablet | [Televizo](https://play.google.com/store/apps/details?id=com.ottplay.ottplay) | Yes | Free |
| Either, for a quick look | [VLC](https://www.videolan.org/vlc/) | No | Free |

Each app needs the playlist address, `http://<your address>:8780/curated.m3u` or a profile's such as `http://<your address>:8780/kids.m3u`. The apps with a guide also take the guide address, `http://<your address>:8780/guide.xml.gz` or the profile's own, such as `http://<your address>:8780/kids.guide.xml.gz`. kia's status page shows both with Copy buttons, so the easiest way is to open the status page on the phone, copy, and paste.

## UHF (iPhone and iPad)

1. Install **UHF - Love your IPTV** from the App Store.
2. Add a playlist, choose M3U, and paste the playlist address.
3. If the guide is empty, add the guide address as the playlist's EPG source.

## Televizo (Android)

1. Install **Televizo - IPTV player** from the Play Store.
2. Add a new playlist, choose M3U, and paste the playlist address.
3. Add the guide address as the playlist's EPG source.

## VLC

- iPhone and iPad: open the **Network** tab, choose **Open Network Stream**, and paste the playlist address.
- Android: open **More**, choose **New stream**, and paste the playlist address.

VLC plays the first channel; swipe or open the playlist to pick another.

## Away from home

These apps reach kia only on your home Wi-Fi. To watch elsewhere, install [Tailscale](https://tailscale.com) on the phone and on the kia machine, and use the kia machine's Tailscale name or address in place of `<your address>`. The guide and Pluto TV channels use the address in `KIA_PUBLIC_URL`; [away from home](players.md#away-from-home) explains how to make that work too. Don't open a port for kia on your router.

Streams on mobile data use a lot of it: an hour of HD TV can be a gigabyte or more.

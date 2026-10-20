# Choosing a player

kia works with any app that can open an M3U playlist. Most also show kia's program guide. Pick by the device you watch on:

| Device | App | Guide | Cost | Setup |
|---|---|---|---|---|
| TV box, Fire TV, Android TV, Raspberry Pi, computer | Kodi | Yes | Free | [Kodi](kodi.md) |
| Android TV, Google TV, Nvidia Shield | TiviMate | Yes | Free; some features paid | [TiviMate](tivimate.md) |
| Any TV or phone with Plex | Plex, through Threadfin | Yes | Free to watch; recording needs Plex Pass | [Plex](plex.md) |
| Any TV or phone with Jellyfin or Emby | Jellyfin or Emby | Yes | Jellyfin is free; Emby needs Emby Premiere | [Jellyfin and Emby](jellyfin.md) |
| iPhone, iPad, Apple TV | UHF | Yes | Free; PRO plan optional | [Phones and tablets](phones.md) |
| Android phone or tablet | Televizo | Yes | Free | [Phones and tablets](phones.md) |
| Computer, phone, or tablet, for a quick look | VLC | No | Free | [VLC](vlc.md) |

Every app needs the same two addresses, which kia's status page shows with Copy buttons:

- The **playlist**: `http://<your address>:8780/curated.m3u`, or a [profile's](profiles.md) playlist such as `http://<your address>:8780/kids.m3u`.
- The **guide**: `http://<your address>:8780/guide.xml.gz`, or a profile's own guide such as `http://<your address>:8780/kids.guide.xml.gz`. Kodi and many apps find it through the playlist; others ask for it.

A smaller playlist from a profile makes any app quicker to load and easier to browse, especially on a phone or a slow TV stick.

## Away from home

kia is meant for your home network. To watch on a phone or laptop elsewhere, connect it to home with a VPN such as [Tailscale](https://tailscale.com), then use the same addresses (or the kia machine's Tailscale name). The playlist itself points at `KIA_PUBLIC_URL` for the guide and for [Pluto TV channels](how-it-works.md#pluto-tv), so that address has to work away from home too: either let Tailscale reach your home network (a [subnet router](https://tailscale.com/kb/1019/subnets)), or set `KIA_PUBLIC_URL` to the Tailscale name, which then has to work at home as well. Don't open a port for kia on your router: anyone who found it could use your connection.

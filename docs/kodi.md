# Setting up Kodi

Do this once on each Kodi device. After that, the channel list and guide update by themselves.

You need the address of the machine running kia, e.g. `http://192.168.1.20:8780` (the `KIA_PUBLIC_URL` from your `.env`). Check it first: opening `http://192.168.1.20:8780/curated.m3u` in a browser on the same network should download a file.

## 1. Install Kodi

Get it from [kodi.tv/download](https://kodi.tv/download/), which has instructions for each platform:

- Windows, macOS, Linux: download the installer (on Linux, often from your distribution's package manager or Flathub).
- Android phones, tablets, Android TV, Google TV, Nvidia Shield: Google Play Store.
- Amazon Fire TV: Kodi isn't in Amazon's store. Install it with the free *Downloader* app; kodi.tv's Android page and the [Kodi wiki](https://kodi.wiki/) walk through it.
- Raspberry Pi or a dedicated TV box: [LibreELEC](https://libreelec.tv/) is a ready-made system that boots straight into Kodi.
- Apple TV and iPhone: possible, but it needs sideloading; see the Kodi wiki.

## 2. Install IPTV Simple Client

*Settings → Add-ons → Install from repository → PVR clients → PVR IPTV Simple Client → Install.*

## 3. Point it at the playlist

Open the add-on's settings (*Settings → Add-ons → My add-ons → PVR clients → PVR IPTV Simple Client → Configure*), then on the **General** tab:

- **Location:** Remote path
- **M3U play list URL:** `http://192.168.1.20:8780/curated.m3u`

<!-- screenshot: IPTV Simple Client, General tab, with Location = Remote path and the M3U URL filled in (docs/images/kodi-general.png) -->

Leave the **EPG** tab's location empty. The playlist tells Kodi where the guide is.

If you'd rather set the guide explicitly (or your Kodi doesn't pick it up), on the **EPG** tab set **Location** to Remote path and **XMLTV URL** to `http://192.168.1.20:8780/guide.xml.gz`.

## 4. Restart Kodi

Restart Kodi (or disable and re-enable the add-on). Channels appear under TV, and the guide fills in within a minute or two.

<!-- screenshot: Kodi TV guide showing channels with program data (docs/images/kodi-guide.png) -->

## Troubleshooting

- No channels? Open the playlist URL in a browser on the same network. If it doesn't download, check `KIA_PUBLIC_URL` and `KIA_BIND=0.0.0.0` in `.env`, and that your firewall allows the port.
- Channels but no guide? Give it a couple of minutes, then restart Kodi. Some channels have no guide data anywhere and show their own name instead of programs. `http://<address>:8780/guide-report.md` lists coverage.
- A channel won't play? Free streams come and go. The nightly check drops streams that stay broken.

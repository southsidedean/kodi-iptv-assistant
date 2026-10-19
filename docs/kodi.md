# Setting up Kodi

Do this once on each Kodi device. With the refresh setting in step 3, the channel list and guide then stay current without you doing anything.

You need the address of the machine running kia, e.g. `http://192.168.1.20:8780` (the `KIA_PUBLIC_URL` from your `.env`). Check it first: opening `http://192.168.1.20:8780/curated.m3u` in a browser on the same network should download a file.

## 1. Install Kodi

Get it from [kodi.tv/download](https://kodi.tv/download/), which has instructions for each platform:

- Windows, macOS, Linux: download the installer (on Linux, often from your distribution's package manager or Flathub). Kodi from Ubuntu's or Debian's own packages needs IPTV Simple Client installed separately: `sudo apt install kodi-pvr-iptvsimple`, then turn it on under *Settings → Add-ons → My add-ons → PVR clients*.
- Android phones, tablets, Android TV, Google TV, Nvidia Shield: Google Play Store.
- Amazon Fire TV: Kodi isn't in Amazon's store. Install it with the free *Downloader* app; kodi.tv's Android page and the [Kodi wiki](https://kodi.wiki/) walk through it.
- Raspberry Pi or a dedicated TV box: [LibreELEC](https://libreelec.tv/) is a ready-made system that boots straight into Kodi.
- Apple TV and iPhone: possible, but it needs sideloading; see the Kodi wiki.

## 2. Install IPTV Simple Client

*Settings → Add-ons → Install from repository → PVR clients → IPTV Simple Client → Install.*

![The PVR clients list in Kodi, with IPTV Simple Client selected](../images/kodi-iptv-addon-configuration.jpg)

## 3. Point it at the playlist

Open the add-on (*Settings → Add-ons → My add-ons → PVR clients → IPTV Simple Client*) and choose **Configure**.

![The IPTV Simple Client add-on page, with the Configure button highlighted](../images/kodi-iptv-iptv-configure.jpg)

On the **General** tab, set:

- **Location:** Remote path (Internet address)
- **M3U playlist URL:** your kia address followed by `/curated.m3u`, e.g. `http://192.168.1.20:8780/curated.m3u`

The **Name** at the top is just a label for this setup; anything works.

Further down the same tab, under **Refresh**, set:

- **Auto refresh mode:** Once per day
- **Refresh hour (24h):** a couple of hours after kia's nightly run. kia runs at 4:00 by default (`KIA_RUN_AT`), so 6 is a good choice.

Without this, Kodi loads the channel list and guide only when it starts. A TV box or streaming stick that sleeps instead of shutting down could go days without picking up kia's nightly refresh. If you change `KIA_RUN_AT`, change the refresh hour on each device to match. If a device is off at the refresh hour, it loads the latest list the next time Kodi starts.

![IPTV Simple Client settings, General tab, with Location set to Remote path and the playlist URL filled in](../images/kodi-iptv-general-configuration.jpg)

On the **EPG** tab, leave **XMLTV URL** empty. The playlist tells Kodi where the guide is. (If your Kodi doesn't pick the guide up, enter `http://192.168.1.20:8780/guide.xml.gz` there, with your address.)

![IPTV Simple Client settings, EPG tab, with the XMLTV URL left empty](../images/kodi-iptv-epg-configuration.jpg)

## 4. Restart Kodi

Restart Kodi (or disable and re-enable the add-on). Channels appear under TV, and the guide fills in within a minute or two. Channels without guide data show their own name in the guide, like IraneFarda TV and It Is Written TV below. Radio stations, if you've added any, appear under Radio.

![The Kodi TV guide with program listings, and a few channels showing their own name](../images/kodi-iptv-guide.jpg)

## 5. Keep channel numbers steady (optional)

kia gives every channel a number that stays the same from night to night, even when other channels come and go. Kodi ignores those numbers until you turn them on. Otherwise it numbers channels 1, 2, 3 in list order, so a channel's number can change after any nightly refresh.

1. Go to *Settings → PVR & Live TV*. If the menu on the left has no *Channels* page, change the settings level at the bottom left to *Advanced*.
2. On the *Channels* page, turn on **Use channel numbers from backend**.

TV and radio are numbered separately, each starting at 1. A new channel gets the next unused number, and a channel that drops out keeps its number for a month, in case it comes back.

## Troubleshooting

No channels, an empty guide, or old channels that won't go away? See [troubleshooting](troubleshooting.md), which starts with the problems you see in Kodi.

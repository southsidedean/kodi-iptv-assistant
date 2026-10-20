# Installing kia

This guide takes you from nothing to a working channel list and program guide in Kodi. It takes about 30 minutes of your time, plus up to an hour for kia's first run, which you don't need to watch.

## What you need

| You need | Details | Why |
|---|---|---|
| A computer to run kia | macOS, Linux, or a Raspberry Pi 4/5 (64-bit) with 4 GB of memory or more | It tests streams and builds the guide every night. It can be the computer you watch on, or one that stays on for the whole house ([setups](setups.md)). |
| Docker | Docker Desktop on macOS; Docker Engine on Linux | Runs kia and everything it needs in one package, so you don't install the pieces yourself. |
| Git | Any recent version | Downloads kia and makes updating a one-line command. |
| Kodi | Version 21 or newer is a good choice | Plays the channels. Install it on each device you watch on ([Kodi setup](kodi.md)). |
| About 2 GB of disk space | | For kia, its downloads, and the guide. |

Kodi itself can run on almost anything, including Windows, Android TV, and Fire TV. Only the computer running kia needs macOS or Linux.

If you plan to run kia on a different computer from the one you watch on, do steps 1 to 6 on that computer.

## The quick way

Once Docker and Git are installed (steps 1 and 2 below), one command does steps 3 to 6: it downloads kia to a `kodi-iptv-assistant` folder in your home folder, finds this computer's address and time zone, makes `.env`, opens the firewall ports if it can, and starts kia. Open a terminal and run:

```sh
curl -fsSL https://raw.githubusercontent.com/southsidedean/kodi-iptv-assistant/main/scripts/install.sh | sh
```

It asks you to confirm the address and time zone, and at the end prints the status page's address and the password for making changes. Running it again is safe; it keeps your settings. Then go on to [step 7](#7-set-up-kodi).

To do it by hand instead, or to see what it does, follow the steps below.

## 1. Install Docker

### macOS

1. Download Docker Desktop for Mac from [docker.com](https://www.docker.com/products/docker-desktop/). Pick *Apple silicon* for M-series Macs or *Intel chip* for older ones (Apple menu → About This Mac shows which).
2. Open the downloaded file, drag Docker to Applications, then start it and wait until the whale icon in the menu bar stops animating.

### Linux (Ubuntu, Debian, Raspberry Pi OS, and most others)

Docker's install script sets up Docker Engine and Docker Compose. It's downloaded with `curl`; if `curl --version` says the command isn't found, install it first with `sudo apt install curl` (or your distribution's equivalent).

```sh
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in (or restart) so the second command takes effect.

### Check it

Open a terminal (on macOS, the *Terminal* app) and run:

```sh
docker compose version
```

It should print a version number. If it says the command isn't found or Docker isn't running, start Docker Desktop (macOS) or restart the computer (Linux).

## 2. Install Git

- macOS: run `git --version` in Terminal. If Git isn't installed, macOS offers to install it; accept.
- Linux: `sudo apt install git` (Ubuntu, Debian, Raspberry Pi OS) or your distribution's equivalent.

Check with `git --version`.

## 3. Download kia

In the terminal, go to the folder where you want kia to live (your home folder is fine) and run:

```sh
git clone https://github.com/southsidedean/kodi-iptv-assistant.git
cd kodi-iptv-assistant
```

The rest of the commands in this guide run from inside this `kodi-iptv-assistant` folder.

## 4. Find this computer's address

Kodi needs an address to reach kia. If Kodi runs on this same computer, use `localhost` and skip ahead. Otherwise, find this computer's address on your network:

- macOS: run `ipconfig getifaddr en0` (or `en1` if that prints nothing), e.g. `192.168.1.20`.
- Linux: run `hostname -I` and use the first address.

This address can change when the computer restarts. To keep it fixed, set up a DHCP reservation in your router's settings (often under *LAN* or *DHCP*).

## 5. Configure kia

Make your settings file from the example:

```sh
cp .env.example .env
```

Open `.env` in a text editor (`open -e .env` on macOS, `nano .env` on Linux) and change these lines:

```ini
KIA_PUBLIC_URL=http://192.168.1.20:8780
TZ=America/New_York
```

Use your address from step 4 (or `localhost`) and your [time zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). If Kodi runs on this computer only, also set `KIA_BIND=127.0.0.1`. Set `PUID` and `PGID` to the numbers that `id -u` and `id -g` print. They tell kia which user should own the files it creates, so you can open and delete them without `sudo`. Many computers use 1000 for both, which is the default.

Choosing playlists is optional: the USA list is on by default, which is enough to start, and you can change it any time. To choose now, make your own copy of the list, so updating kia never touches your choices:

```sh
cp sources.txt sources.local.txt
```

In `.env`, set `KIA_SOURCES_FILE=sources.local.txt` (the line is already there, empty). Then open `sources.local.txt` and remove the `#` at the start of each playlist you want. The USA list is on already. Each line shows roughly how many streams it has; more streams make the nightly check longer. The same file can add radio stations, as its comments explain.

## 6. Start kia

```sh
docker compose up -d --build
```

The first time, this downloads and builds kia (a few minutes), then starts the first run straight away. That run checks every stream and downloads the guide, which can take up to an hour. To watch its progress, run `docker compose logs -f kia` (press Ctrl+C to stop watching; kia keeps running).

When the log says `Next run at ...`, it's done. Check it in a browser at `http://<your address>:8780/`. You should see kia's [status page](how-it-works.md#the-status-page), with the channel counts and your playlist's address. If another device will use kia, open that address on it too.

kia also made a password for changing things on the status page, such as [playlists for people and devices](profiles.md). It's in `state/admin-password.txt` in the kia folder; [the secure status page](secure-page.md) explains it.

If other devices can't connect (more in [troubleshooting](troubleshooting.md#other-devices-cant-connect)):

- Linux with a firewall (ufw): `sudo ufw allow 8780/tcp` and `sudo ufw allow 8743/tcp` (the secure status page).
- Check that `KIA_BIND` in `.env` is `0.0.0.0`, then run `docker compose up -d` again.

From now on kia refreshes everything once a day at `KIA_RUN_AT` (4:00 by default). Pick a time when nobody is watching, because a run keeps the computer busy for up to an hour. Between runs, kia refreshes the guide every 4 hours (`KIA_GUIDE_REFRESH_HOURS`). Kodi picks up each update within two hours, with the setting in the next step. Docker restarts kia after a reboot, as long as Docker itself starts with the computer (on macOS, turn on Docker Desktop's *Start Docker Desktop when you sign in* setting).

## 7. Set up Kodi

Follow [Kodi setup](kodi.md) on each device you watch on: install Kodi, add IPTV Simple Client, enter `http://<your address>:8780/curated.m3u`, and turn on the repeated refresh. For Plex, Jellyfin, TiviMate, phones, and other apps, see [choosing a player](players.md).

## Next steps

- [Playlists for people and devices](profiles.md): a short list for the kids, a room, or a phone.
- [Setups](setups.md): laptops that travel, Raspberry Pi settings, running without Docker.
- [Configuration](configuration.md): every setting.
- [How it works](how-it-works.md): what happens each night, and the reports.
- [Troubleshooting](troubleshooting.md): what to do when something isn't working.

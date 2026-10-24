# Security

kia runs on your home network, and every night it runs third-party code: iptv-org's guide downloader.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

iptv-org's guide downloader runs in Node.js's sandbox (`node --permission`): it can read only its own folder and the channel list it's given, write only the guide it makes and a temporary folder, and can't start other programs. It also sees none of kia's settings, so it can't read your passwords or tokens. kia compiles iptv-org's scripts for it with esbuild, iptv-org's own or, when it doesn't bring one, a fixed version kia installs itself. Without Docker, this needs Node.js 22 or newer; kia's log says when the sandbox can't be used.

Both containers run with a read-only system. kia has no extra privileges, and can write only to its `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/` folders; the other programs' data in the kia folder (Jellyfin's, Grafana's) is hidden from it, and the programs that open streams from the internet (ffprobe, ffmpeg) get none of its passwords or tokens. The web server (nginx) starts as root to open its ports, keeping only the capabilities that takes, then works as its own user; it can read only `output/` and the certificate in `tls/`, and sends headers that keep kia's pages from loading anything from other sites or being shown inside one. The optional services are looser where they have to be. Threadfin's page asks for a login that kia makes the first time (`threadfin/conf/admin-password.txt`). Grafana answers only over HTTPS, with a random admin password kia makes the first time (`monitoring/grafana/admin-password.txt`) unless you set `KIA_GRAFANA_PASSWORD`. Jellyfin starts with its setup screens open to anyone on your network until you finish them, so do that right after starting it. Plex in the stack starts as root to set itself up, keeping only the few capabilities that takes. cAdvisor (with the `monitoring` profile) reads Docker's socket to name each container, which amounts to full access to the computer, so it gets a read-only system and no capabilities. node-exporter sees the computer's processes and network (`KIA_NODE_EXPORTER_LISTEN` keeps it off the rest of your network). The disks' health exporter (`monitoring-disks`) runs privileged, as root, because reading SMART data takes the disks themselves: full access to the computer, with a read-only system and no published port. The graphics card's exporter (`monitoring-gpu`) gets the card and nothing else: no capabilities, a read-only system. Turn on only what you use. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

The optional kia-stack service ([changing the stack from the Dashboard](docs/stack.md#changing-the-stack-from-the-dashboard)) runs on the computer as you, with access to Docker, which the kia container doesn't have. It acts on requests kia leaves in `state/`, and treats them as untrusted, since a broken kia could write anything there: it reads them as plain lines, never runs them, and accepts only `check` or `apply` with part names it knows. So the most kia can do through it is start or stop the parts listed in [choosing the parts](docs/stack.md), as `scripts/stack.sh apply` would. Without the service, nothing reads those requests.

From the Dashboard you can change [profiles](docs/profiles.md), which playlists kia checks (including adding a playlist by its address), favorites and channel numbers, and the password, and you can test a channel or take it out of the playlists. Settings in `.env` and `kia.toml` can't be changed there. Changes need the admin password and are accepted only on the secure (HTTPS) page, so the password isn't sent in the clear; kia believes that a request came through the secure page only from its own web server, not from other containers. Channel source addresses can hold a provider's user name and password; kia shows them in full only on the secure page and only after the password, and hides them everywhere else, **Check now**'s progress included. kia keeps only a scrambled (scrypt) form of the password, refuses requests from other websites, and slows down guessing: a second after each wrong password, and from the second one in a row, a wait that doubles each time, up to a minute, for the address that sent them. kia's own certificate is self-signed, so a browser can't tell it apart from a fake one the first time; on a home network that's a small risk, and [your own certificate](docs/secure-page.md#using-your-own-certificate) removes it.

Published images are scanned before they're pushed, and carry an SBOM and a signed build attestation ([how to check one](docs/configuration.md#the-published-image)).

Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale.

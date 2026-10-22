# Security

kia runs on your home network, and every night it runs third-party code: iptv-org's guide downloader.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

iptv-org's guide downloader runs in Node.js's sandbox (`node --permission`): it can read only its own folder and the channel list it's given, write only the guide it makes and a temporary folder, and can't start other programs. It also sees none of kia's settings, so it can't read your passwords or tokens. Without Docker, this needs Node.js 20 or newer; kia's log says when it can't be used.

Both containers run with a read-only system and no extra privileges. kia can write only to its `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/` folders, and the web server can read only `output/` and the certificate in `tls/`. The optional services are looser where they have to be: Plex in the stack starts as root to set itself up; cAdvisor (with the `monitoring` profile) reads Docker's own folders and socket to measure each container, which amounts to full access to the computer; node-exporter sees the computer's processes and network. Turn on only what you use. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

From the Dashboard you can change [profiles](docs/profiles.md), which playlists kia checks (including adding a playlist by its address), favorites and channel numbers, and the password, and you can test a channel or take it out of the playlists. Settings in `.env` and `kia.toml` can't be changed there. Changes need the admin password and are accepted only on the secure (HTTPS) page, so the password isn't sent in the clear. Channel source addresses can hold a provider's user name and password; kia shows them in full only on the secure page and only after the password, and hides them everywhere else, **Check now**'s progress included. kia keeps only a scrambled (scrypt) form of the password, refuses requests from other websites, and waits a second after each wrong password to slow down guessing. kia's own certificate is self-signed, so a browser can't tell it apart from a fake one the first time; on a home network that's a small risk, and [your own certificate](docs/secure-page.md#using-your-own-certificate) removes it.

Published images are scanned before they're pushed, and carry an SBOM and a signed build attestation ([how to check one](docs/configuration.md#the-published-image)).

Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale.

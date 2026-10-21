# Security

kia runs on your home network, and every night it runs third-party code: iptv-org's guide downloader.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

Both containers run with a read-only system and no extra privileges. kia can write only to its `output/`, `state/`, `work/`, `vendor/`, `profiles/`, and `tls/` folders, and the web server can read only `output/` and the certificate in `tls/`. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

From the status page you can change [profiles](docs/profiles.md), which playlists kia checks (including adding a playlist by its address), favorites and channel numbers, and the password, and you can test a channel or take it out of the playlists. Settings in `.env` and `kia.toml` can't be changed there. Changes need the admin password and are accepted only on the secure (HTTPS) page, so the password isn't sent in the clear. kia keeps only a scrambled (scrypt) form of the password, refuses requests from other websites, and waits a second after each wrong password to slow down guessing. kia's own certificate is self-signed, so a browser can't tell it apart from a fake one the first time; on a home network that's a small risk, and [your own certificate](docs/secure-page.md#using-your-own-certificate) removes it.

Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale.

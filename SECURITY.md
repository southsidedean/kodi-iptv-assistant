# Security

kia runs on your home network, and every night it runs third-party code: iptv-org's guide downloader.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

Both containers run with a read-only system and no extra privileges. kia can write only to its `output/`, `state/`, `work/`, `vendor/`, and `profiles/` folders, and the web server sees only `output/`.

The status page can change [profiles](docs/profiles.md) only when `KIA_ADMIN_PASSWORD` is set in `.env`, and only with that password. It can't change any other setting. It refuses requests from other websites, and each wrong password costs a second, which slows down guessing. The page uses plain HTTP, so anyone who can watch your home network's traffic could see the password. Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

# Security

kia runs on your home network and runs third-party code (iptv-org's guide downloader) every night, so security problems matter even for a small project.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

Both containers run with a read-only system and no extra privileges. kia can write only to its `output/`, `state/`, `work/`, and `vendor/` folders, and the web server sees only `output/`. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

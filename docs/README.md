# kia's guides

New here? Begin with [start here](start-here.md).

## Getting going

- [Start here](start-here.md): what kia gives you, the three steps, and the words you'll see.
- [Installing kia](install.md): from nothing to a working channel list, step by step.
- [Setups](setups.md): one server for the house, a Raspberry Pi, a laptop that travels, or no Docker.
- [Your stack from the Dashboard](stack-dashboard.md): Plex, Jellyfin, or graphs, chosen on the Dashboard's Stack page, start to finish.
- [Choosing the parts of kia's stack](stack.md): Plex, Jellyfin, graphs, and more, with one line in `.env` and `scripts/stack.sh`.
- [Cookbooks](cookbooks/README.md): one goal from start to finish, such as [a home TV box with Plex](cookbooks/plex-box.md) or [Kodi and kia on one computer](cookbooks/kodi-computer.md).

## Watching

- [Choosing a player](players.md): which app for which device, and watching away from home.
- [Kodi](kodi.md)
- [Plex](plex.md), through Threadfin
- [Jellyfin and Emby](jellyfin.md)
- [TiviMate](tivimate.md) on Android TV
- [Phones and tablets](phones.md)
- [VLC](vlc.md)

## Everyday use

- [Playlists for people and devices](profiles.md): smaller playlists for the kids, a room, or a phone.
- [The secure Dashboard and your password](secure-page.md)
- [Alerts](alerts.md): a message on your phone when something needs you.
- [Asking kia for help](ask-kia.md): plain answers from the Dashboard, or your own AI assistant reading kia's record.
- [kia and AI assistants (MCP)](mcp.md): setting up Claude Code, Gemini CLI, Claude Desktop, Codex, VS Code, or Cursor to ask kia.
- [Troubleshooting](troubleshooting.md)

## Going further

- [Configuration](configuration.md): every setting.
- [How it works](how-it-works.md): what happens each night, the reports, and the Dashboard.
- [The stack under the hood](stack-under-the-hood.md): how `KIA_STACK`, `scripts/stack.sh`, the Stack page, and the kia-stack service fit together.
- [How the guide gets built](guide-under-the-hood.md): how kia finds each channel's listings, downloads them, and picks the best.
- [The Pluto TV relay under the hood](pluto-under-the-hood.md): how kia keeps Pluto channels playing through ad breaks.
- [Plex and Threadfin under the hood](plex-under-the-hood.md): Threadfin's first settings, how kia adds the tuners to Plex, and the codec warm-up.
- [Security under the hood](security-under-the-hood.md): what kia trusts, where untrusted input goes, and what each layer stops.
- [Using kia from scripts and other apps](api.md)
- [Metrics for Prometheus](metrics.md): kia's numbers, for graphs and alerts in Grafana.

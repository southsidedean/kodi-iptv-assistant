# Security under the hood

How kia's protections fit together: what kia trusts and what it doesn't, where each piece of untrusted input goes, and what a program that went wrong could still reach. [Security](../SECURITY.md) lists the rules and how to report a problem. Read this one to see why they're there.

## What kia trusts

kia works with a lot of material from strangers. It treats everything from outside your own settings as untrusted:

| What | From | Trusted? |
|---|---|---|
| `.env`, `kia.toml`, `sources.txt`, your profiles | you | Yes, though kia still checks each value when it reads it |
| What you do on the secure Dashboard, with the password | you | Yes |
| Playlists from the internet, and the streams in them | anyone who publishes one | No |
| Guide sites, and the guides playlists name | anyone who runs one | No |
| iptv-org/epg's code, updated nightly | many contributors | No |
| Pluto TV's sessions and pieces | Pluto, or anyone between | No |
| Another kia server's guide (`KIA_UPSTREAM_URL`) | another computer | No |
| Plex's and Jellyfin's answers | your servers | Mostly: kia reads only what it asked for |

Most of it is ordinary, and some of it is broken. But kia reads thousands of addresses from strangers every night, so each kind of input gets a guard where it enters, and the programs that handle it get as little as they need.

## The layers

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/security-layers-dark.svg">
  <img alt="kia's layers of protection, from the outside in. Playlists, streams, guide sites, iptv-org's code, and Pluto TV come from the internet. Each enters through a guard: kia's checks on what it reads, ffprobe and ffmpeg with none of kia's settings, and iptv-org's downloader in Node's sandbox. All of them run inside kia's container, which is read-only apart from kia's own folders and has no Docker access. The Dashboard reaches kia through the web server, with the password over HTTPS. Only the kia-stack service, on the computer as you, reaches Docker, and it accepts only a strictly checked request to start or stop parts of the stack." src="../images/diagrams/security-layers-light.svg">
</picture>

## Where untrusted input goes

### Playlists

- Control characters and broken text are removed as kia reads each line. Only `#KODIPROP` lines that tell Kodi how to play a stream are passed on to your playlists; anything else a playlist sets for Kodi is dropped.
- Only network addresses (`http`, `https`, `rtmp`, `rtmps`, `rtsp`, `rtsps`) with a host reach ffprobe. One with no host, such as `http://:9090/`, would make ffmpeg connect to this computer itself, so it's marked dead, as is one kia can't read. A playlist entry like a file path, or any of ffmpeg's special addresses that read files or chain other addresses together, is marked dead without being opened.
- A playlist that isn't yours can't point kia at your network: kia tests its streams, and downloads the guides it names, only at addresses out on the internet ([playlists from the internet](../SECURITY.md#playlists-from-the-internet)). Playlists of your own (a file on this computer, one whose address names your network as written, such as `192.168.1.20` or `my-server.lan`, or a line marked `lan`) can name anything. kia never decides that by looking a name up. Guide downloads refuse a redirect from the internet onto your network.
- A playlist that downloads but holds no stream addresses counts as a failed download, so a broken answer can't replace your last good copy.

### Streams

ffprobe and ffmpeg are the programs most exposed: they read the streams themselves, in formats complicated enough to have had bugs. kia starts them with a bare environment, so none of kia's passwords or tokens are in the settings they're given, and with time limits, so a server that stalls or trickles can't hold a test open. In Docker, they run inside kia's container, with its limits (below).

### Guide sites

- Site names come from iptv-org's files and become file names in `work/`, so kia uses a name only if it looks like a domain, with no `..` or slashes.
- Guides are read as XML with Python's own reader, which doesn't expand entities, so a small file can't turn into a huge one. They're read a piece at a time, and only the channels in your list are kept.
- A download that fails, stops part way, or runs past its time limit never replaces the last good copy.

### iptv-org's code

iptv-org/epg is a large project with many contributors, and kia runs its downloaders every night. So:

- Its Node.js packages are installed without running any of their install scripts.
- Its downloader runs in Node's sandbox: it reads only its own folder and the channel list, writes only its guide and a temporary folder, can't start other programs, and sees none of kia's settings.
- kia compiles iptv-org's scripts for the sandbox with its own copy of esbuild, a fixed version, since compiling runs outside the sandbox.
- Before each update, kia rewrites that copy's git settings from scratch and turns off git's hooks, so nothing planted there runs when git updates it.

What the downloader writes is a guide file, which kia then reads as untrusted like any other.

### Pluto TV

The relay fetches only https addresses (redirects too), at most 16 MB each, plays only the Pluto channels in kia's playlists, and at most 20 at once ([Pluto TV relay](pluto-under-the-hood.md)).

### Another kia server

A guide from `KIA_UPSTREAM_URL` is read like any other guide: as XML, a piece at a time, keeping only the channels in your own list.

## If a layer fails

Suppose a program got taken over, by a stream that broke ffprobe, say. What it could do depends on where it runs:

| Taken over | Could | Couldn't |
|---|---|---|
| iptv-org's downloader, in its sandbox | Read its own folder and the channel list, write its guide, use the network | Read kia's settings or other files, start programs |
| ffprobe or ffmpeg, or kia itself, in Docker | Everything kia can: write `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/`; read the rest of the kia folder, `.env` included, and with Plex in the stack, Plex's settings and sign-in; reach your network; leave requests for the kia-stack service | Change the system or the rest of the kia folder, see Jellyfin's or Grafana's data, reach Docker, gain privileges |
| The same, without Docker (Linux service) | The same folders, read your files, reach your network | Change files outside kia's folders and npm's cache, where the system allows those limits ([without Docker](setups.md#without-docker)) |
| The web server | Read `output/`, the certificate and its key | Change anything kia uses |

That's why kia's container has no Docker access, and why the kia-stack service checks every request as if kia had been taken over. The most a taken-over kia can do through the service is start or stop the parts of the stack you could choose on the Stack page ([the kia-stack service](../SECURITY.md#the-kia-stack-service), [under the hood](stack-under-the-hood.md#why-its-built-this-way)).

## The Dashboard

The Dashboard is how you change things, so it's how someone else would try to:

- Changes need the password, and only over HTTPS. kia trusts the "came over HTTPS" mark only from its own web server, which sets it on every request.
- kia keeps only a scrypt hash of the password, and turns away requests from other websites' pages.
- Wrong passwords slow down: a second each, then a wait that doubles from the second one in a row, up to a minute, for the address that sent them. The web server passes on that address itself, so a browser can't claim someone else's.
- Only a short list of things can be changed: profiles, playlists, favorites and numbers, the password, and the stack. Nothing else in `.env` or `kia.toml`.

The [MCP server](mcp.md), when you turn it on, gives an AI assistant the same view as the Dashboard: read-only, over HTTPS, with its own token. What it quotes from playlists and guide sites can carry planted instructions, so its tools can't change anything, whatever the assistant is told. The assistant itself may be able to do more (Claude Code can run commands), so its own permission questions are the other half of the protection ([security](../SECURITY.md#the-mcp-server)).

## What's left to you

- Who can connect: `KIA_BIND` chooses every device on your network, or this computer only. Keep kia off the internet; to reach it from outside, use a VPN such as Tailscale ([away from home](players.md#away-from-home)).
- Updates: fixes go into the latest release ([updating](../README.md#updating-and-stopping)).
- Only what you use: every part of the stack you turn on is more code running. Some need more access than kia: cAdvisor reads Docker's socket, and the disk health exporter runs privileged ([the containers](../SECURITY.md#the-containers)).
- Backups: a backup holds your password and the secure page's private key, so keep it somewhere private ([backing up](configuration.md#backing-up-and-moving-to-another-computer)).
- Your own playlists: a playlist you mark as yours, or put on your network, can send kia's tests anywhere on your network. Mark only ones you trust.

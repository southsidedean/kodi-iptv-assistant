# Security

kia runs on your home network, and every night it runs third-party code: iptv-org's guide downloader.

## Reporting a problem

Please report security problems privately, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**, or use [this link](https://github.com/southsidedean/kodi-iptv-assistant/security/advisories/new). Please don't open a public issue.

Say what the problem is, how to reproduce it, and which version you use.

## Supported versions

Fixes go into the latest release. Update with the steps in the [README](README.md#updating-and-stopping).

## How kia limits the damage

### The guide downloader

iptv-org's guide downloader runs in Node.js's sandbox (`node --permission`): it can read only its own folder and the channel list it's given, write only the guide it makes and a temporary folder, and can't start other programs. It also sees none of kia's settings, so it can't read your passwords or tokens. kia compiles iptv-org's scripts for the sandbox with its own copy of esbuild, a fixed version, never one that iptv-org's dependencies bring along, since compiling runs outside the sandbox. Before each update of iptv-org's code, kia rewrites that checkout's git settings from scratch, so nothing written there earlier (a "filter" that runs a command, say) runs when git updates it. Without Docker, this needs Node.js 22 or newer; kia's log says when the sandbox can't be used.

### Playlists from the internet

kia tests every stream in the playlists it reads, so a playlist from the internet could have it send requests to the devices around it: your router's settings page, a printer, or the other containers of kia's stack, which Docker lets kia reach by name. To stop that, kia tests a stream from such a playlist only when its address is out on the internet. One on your network, on this computer, or in Docker's networks is left out, and the report says why. The same goes for the guides such a playlist names in its header, which kia downloads too. Playlists of your own can name anything: a file on this computer, or one downloaded from an address on your network (a tuner, Threadfin, tvheadend). kia goes by the address as it's written: a local IP address such as `192.168.1.20`, or a name only your network has, such as `nas` or `my-server.lan`. It doesn't look the name up, because whoever runs a name's DNS could answer with a local address. For any other playlist whose streams really are on your network, add the word `lan` to its line in the sources file. Addresses that carry a local IPv4 address inside an IPv6 one (NAT64, 6to4) count as local, and an address with no host at all, or one kia can't read, is never tested.

That check alone could be got around. kia looks each name up before the test, then ffprobe looks it up again, and ffprobe follows redirects and opens an HLS playlist's pieces by itself, out of kia's sight. So the http and https streams of a playlist that isn't yours, and the guides it names, also go through kia's outbound guard: a small proxy inside kia, on this computer only. The guard looks each name up once and connects to the address it checked, so a DNS server that answers differently the second time gains nothing. It checks every connection, each redirect and each HLS piece too, and refuses any on your network; the report says so. kia's own downloads of things that aren't yours go through it as well: playlists from the internet, radio station lists, and the Pluto relay's downloads. ffprobe may use only plain http, which goes to the guard; the guard opens https addresses for it, with the certificate checked, and rewrites the addresses in HLS playlists to come back through it. So ffprobe has no way to open a file, a raw connection, or a connection past the guard. Two cases keep the first check alone: rtmp and rtsp streams, which can't go through a web proxy (they're rare in free playlists), and every stream when you've set a proxy of your own (`http_proxy` and the like), which kia leaves in charge ([the outbound guard](docs/security-under-the-hood.md#the-outbound-guard)).

### The containers

kia's two containers (kia, and the web server) run with a read-only system, and with limits on memory and on how many processes they run at once (kia 4 GB, `KIA_MEMORY_LIMIT`, and 1,024; the web server 256 MB and 256), so a program run wild can't take over the computer. kia has no extra privileges, and can write only to its `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/` folders. It sees the rest of the kia folder read-only, `.env` included (it needs it for backups), and with Plex in the stack, Plex's settings, where it reads Plex's sign-in, and Threadfin's settings, which hold the password for Threadfin's page (`kia doctor plex` reads which playlist Threadfin uses). Jellyfin's and Grafana's data are hidden from it. The programs that open streams from the internet (ffprobe, ffmpeg) get none of kia's passwords or tokens.

Without Docker, on Linux, the kia service (`deploy/kia.service`) gets systemd's limits instead: no extra privileges, only the system calls an ordinary service makes, and the computer and your home folder read-only except the folders kia writes (the ones above, without `plex/warmup/`) and npm's cache. On macOS, kia runs as you, with no limits beyond that. The file limits need the kernel to let a user service make a user namespace, which Ubuntu 23.10 and newer don't allow (AppArmor). There systemd skips them without a word, so kia can write anywhere you can; `scripts/install-service.sh` says so when it installs. For the full lockdown on Ubuntu, run kia with Docker.

The web server (nginx) starts as root to open its ports, keeping only the capabilities that takes, then works as its own user. It reads `output/`, the certificate and key in `tls/`, and its own settings, and sends headers that keep kia's pages from loading anything from other sites or being shown inside one.

The optional services are looser where they have to be:

- Threadfin's page asks for a login that kia makes the first time (`threadfin/conf/admin-password.txt`).
- Grafana answers only over HTTPS, with a random admin password kia makes the first time (`monitoring/grafana/admin-password.txt`) unless you set `KIA_GRAFANA_PASSWORD`.
- Jellyfin starts with its setup screens open to anyone on your network until you finish them, so do that right after starting it.
- Plex in the stack starts as root to set itself up, keeping only the few capabilities that takes.
- cAdvisor (part of `observability`) reads Docker's socket to name each container, which amounts to full access to the computer, so it gets a read-only system and no capabilities.
- node-exporter sees the computer's processes and network (`KIA_NODE_EXPORTER_LISTEN` keeps it off the rest of your network).
- The disks' health exporter (`observability-disks`) runs privileged, as root, because reading SMART data takes the disks themselves: full access to the computer, with a read-only system and no published port.
- The graphics card's exporter (`observability-gpu`) gets the card and nothing else: no capabilities, a read-only system.

Who can reach these ports is up to `KIA_BIND` in `.env`: `0.0.0.0` for every device on your network, `127.0.0.1` for this computer only. On Linux, ufw doesn't change it either way, since Docker lets connections to the ports it publishes past ufw by itself. Prometheus answers on this computer only, whatever `KIA_BIND` says.

Turn on only what you use. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

### The kia-stack service

The optional kia-stack service ([changing the stack from the Dashboard](docs/stack.md#changing-the-stack-from-the-dashboard)) runs on the computer as you, with access to Docker, which the kia container doesn't have. It's the one way from the Dashboard to Docker, so it assumes the kia container could have been taken over (by a stream or guide site that broke one of the programs in it), and could have left anything in `state/`: links, folders, odd files.

- The service never writes, reads, or follows anything in `state/`. All it does there is rename a request away into its own folder, `stack-service/`, which kia sees read-only. A rename moves a link itself, not what it points to, and kia can't put anything at the destination. Something that isn't a plain file is removed, unread.
- It reads only the first 4 KB of a request, as plain lines that are never run. A request has to say `check` or `apply`, with part names it knows, and be less than 5 minutes old.
- A request can carry a Plex claim code, for an apply that starts Plex for the first time. The code has to look like one from plex.tv/claim, and it works for only 4 minutes. kia writes the request so only you can read it, and the service keeps it only while it works on it. A code that's used goes into `.env`, which only you can read, and the service never prints it.
- It runs `scripts/stack.sh check` or `apply` with those parts, and writes its answer, its list of containers, its own lock, its log, and the copy of `.env` an apply keeps only in its own folder, where nothing already at a name is written through.

So the most a taken-over kia can do through it is what you could do with `scripts/stack.sh apply` and the parts in [choosing the parts](docs/stack.md#the-parts): start or stop them. That includes `observability-disks`, which runs privileged (above). An apply from the Dashboard doesn't stop to ask about viewers the way the script does in a terminal; the Stack page asks instead. Without the service, nothing reads those requests.

`scripts/update.sh` also keeps what it needs between runs where kia can't write (in git's own folder), and checks it before use.

### The Dashboard

From the Dashboard you can change [profiles](docs/profiles.md), which playlists kia checks (including adding a playlist by its address), favorites and channel numbers, the password, this kia's name, and with the kia-stack service, `KIA_STACK`; you can also test a channel or take it out of the playlists, and approve or decline an AI assistant's requests. No other setting in `.env` or `kia.toml` can be changed there.

- Changes need the admin password and are accepted only on the secure (HTTPS) page, so the password isn't sent in the clear. kia believes that a request came through the secure page only from its own web server, which sets that on each request itself, not from other containers or the browser.
- kia keeps only a scrambled (scrypt) form of the password, and refuses changes from other websites' pages. What the plain page and its API show anyone on your network (playlists, the guide, kia doctor's answers, `/metrics`) a website could also read through DNS rebinding, a trick that makes its own name point at kia; changes can't be made that way, since they need the password over HTTPS.
- It slows down guessing: a second after each wrong password, and from the second one in a row, a wait that doubles each time, up to a minute, for the address that sent them. The web server passes on that address itself, so a browser can't claim someone else's, and guesses sent all at once take their turn like any others. IPv6 addresses count by their /64, the block one computer picks addresses from. Where every client shares one address (Docker Desktop on a Mac shows kia its own, for example), someone guessing on your network also makes the owner wait; letting a right password through during the wait would let a guesser tell right from wrong.
- Channel source addresses can hold a provider's user name and password; kia shows them in full only on the secure page and only after the password, and hides them everywhere else, **Check now**'s progress included.
- kia's own certificate is self-signed, so a browser can't tell it apart from a fake one the first time; on a home network that's a small risk, and [your own certificate](docs/secure-page.md#using-your-own-certificate) removes it.

### The MCP server

kia's [MCP server](docs/mcp.md) lets an AI assistant read kia's record, and ask for a few things you approve. It's off until you make its token (`kia mcp-token`), and then it answers only on the secure (HTTPS) port, only with that token. `kia mcp` is the same server for an assistant on this computer, over its standard input and output: no network and no token, so it's for an assistant you'd let run kia's commands anyway.

- A few tools ask for something: `kia_test_channel`, `kia_refresh_guide`, `kia_add_to_plex`, and `kia_check_stack`. They don't do it: they leave a request in `state/asks/`, which runs only once you approve it on the Dashboard, with the password over HTTPS, using the same code as the Dashboard's own button. A request says in kia's words what it will do, its arguments are checked when it's made and again when it runs, it expires after 10 minutes, and at most 4 wait at once, one of each kind (`[asks]` in `kia.toml` can change the time, and make the number fewer). No tool can ask for a change to your setup: applying a stack choice, passwords, settings, or sources. `kia_check_stack` asks the kia-stack service only to check a choice, the same request the Stack page's Check makes.
- You can let stream tests, guide refreshes, and stack checks run without you: for an hour from the Dashboard (with the password), or always with `auto_approve` in `kia.toml`, optionally only for some gateway devices (`auto_devices`; a device's name counts only when the gateway's own token sent it). Adding kia's channels to Plex always waits. Each still becomes a request on the Dashboard, marked approved automatically, and at most `auto_per_hour` (20) of a kind run by themselves in an hour. The settings are in `kia.toml`, which kia's container can't write; the hour's allowance is in `state/`, and kia ignores any entry longer than an hour or for a kind that always waits. The hour covers one caller: a gateway device, or else an address. kia offers it only for an address that stands for one device, never under Docker Desktop (which shows kia one address for everyone) or for calls from the kia computer itself. Anyone on your network can see the requests waiting on the Dashboard, as they see the rest of it.
- The other tools only read, and they return what the Dashboard already shows to anyone on your network. Passwords, tokens, and alert addresses show only as set or not set (some settings come from `.env`, shown as on the System page), and user names and passwords in addresses are hidden: before the host, in the query (`?username=&password=`), and in the path (`/live/user/pass/`).
- Some tools ask programs in your own stack, with the settings kia already has: `kia_hardware` asks Prometheus; `kia_jellyfin` asks Jellyfin; `kia_find_shows` asks Plex and Jellyfin for their guide listings, and `kia_whats_on` asks Plex; `kia_overview` asks Prometheus and Jellyfin for their problems, as the open `GET /api/doctor` does. They only read, the tokens stay in kia, and what comes back is what Grafana's and those programs' own pages show. What plays on Plex or Jellyfin isn't in any tool; `kia_pluto` says which Pluto TV channels are playing through kia now, and for how many devices, as `pluto.json` does.
- A wrong token is slowed down like a wrong password, counted apart from wrong passwords (so a token holder can't use right tokens to reset the count while guessing the password), and each address can make 60 tool calls a minute. Where every client shares one address (under Docker Desktop, say), they share that limit too; the gateway gives each device its own. kia's log records each call and each refused token, with the address it came from.
- Requests from other websites' pages are refused, as for the rest of kia's API.
- What it returns quotes text from strangers: channel names, guide titles, guide sites' errors. An assistant could read instructions planted there, and a taken-over kia container controls everything the tools return. kia tells the assistant at the start that this text is data, and kia's tools can't change anything. But an assistant such as Claude Code can also run commands and reach the internet, so a planted instruction that fooled it could try to use those. Keep the assistant asking before it runs commands, and don't approve one that came from an answer about kia unless kia's docs give it.
- The token is kept in `state/mcp-token.txt`, readable only by you, and left out of backups. `kia mcp-token --reset` replaces it; `--off` turns the server off.

### The gateway part

The optional `gateway` part puts [agentgateway](https://agentgateway.dev) in front of the MCP server ([several devices](docs/mcp.md#several-devices-the-gateway)). kia's own protections stay in place behind it, and kia checks every call's token as before.

- Each device has a key of its own, kept only as its SHA-256 in `gateway/`, readable only by you. Removing a device stops its key at once. The gateway logs each call with the device that made it, and limits each device (`KIA_GATEWAY_RATE`).
- The gateway has its own token for kia (`gateway/kia-token`), which no device sees, and kia accepts it only while the gateway part is chosen. With kia's own token turned off (`kia mcp-token --off`), the gateway is the only way in.
- It runs read-only, as you, with no capabilities, on a Docker network of its own shared only with kia and its web server. It reaches kia's MCP server through the web server over HTTPS, trusting only the certificate in `tls/`. Its admin page is off.
- kia sees every call through the gateway as coming from the gateway's address. The gateway names the device in a header, which kia believes only with the gateway's token, so kia's limit of 60 tool calls a minute applies to each device. Wrong tokens are still counted for the gateway's address.
- It's a third-party program, pinned to a tested version (`KIA_GATEWAY_IMAGE`). Without the part, it doesn't run and nothing in `gateway/` is used.

### Pluto TV

The [Pluto TV relay](docs/how-it-works.md#pluto-tv) fetches only https addresses (redirects included), plays only the Pluto channels in kia's playlists, at most 20 at once, downloads at most 16 MB for any one address, keeps at most 32 MB of pieces for each channel, and keeps only keys of the right size, so a server that sends too much can't fill kia's memory.

### Published images

Published images are scanned before they're pushed, and carry an SBOM and a signed build attestation ([how to check one](docs/configuration.md#the-published-image)).

Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale.

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

kia tests every stream in the playlists it reads, so a playlist from the internet could have it send requests to the devices around it: your router's settings page, a printer, or the other containers of kia's stack, which Docker lets kia reach by name. So a stream in such a playlist is tested only when its address is out on the internet; one on your network, on this computer, or in Docker's networks is left out, and the report says why. Playlists of your own can name anything: a file on this computer, or one downloaded from an address on your network (a tuner, Threadfin, tvheadend). The word `lan` on a playlist's line in the sources file allows it for one from elsewhere.

It's a guard for the ordinary case, and someone determined can get past it. kia looks each name up before the test and ffprobe looks it up again, so a DNS server that answers differently the second time gets through, and so does a stream that redirects to an address on your network.

### The containers

kia's two containers (kia, and the web server) run with a read-only system. kia has no extra privileges, and can write only to its `output/`, `state/`, `work/`, `vendor/`, `profiles/`, `tls/`, and `plex/warmup/` folders. It sees the rest of the kia folder read-only, `.env` included (it needs it for backups), and with Plex in the stack, Plex's settings, where it reads Plex's sign-in. Jellyfin's and Grafana's data are hidden from it. The programs that open streams from the internet (ffprobe, ffmpeg) get none of kia's passwords or tokens.

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

Turn on only what you use. [Configuration](docs/configuration.md#versions) explains why, and how to pin versions you've checked instead of following the latest.

### The kia-stack service

The optional kia-stack service ([changing the stack from the Dashboard](docs/stack.md#changing-the-stack-from-the-dashboard)) runs on the computer as you, with access to Docker, which the kia container doesn't have. It's the one way from the Dashboard to Docker, so it assumes the kia container could have been taken over (by a stream or guide site that broke one of the programs in it), and could have left anything in `state/`: links, folders, odd files.

- The service never writes, reads, or follows anything in `state/`. All it does there is rename a request away into its own folder, `stack-service/`, which kia sees read-only. A rename moves a link itself, not what it points to, and kia can't put anything at the destination. Something that isn't a plain file is removed, unread.
- It reads only the first 4 KB of a request, as plain lines that are never run. A request has to say `check` or `apply`, with part names it knows, and be less than 5 minutes old.
- A request can carry a Plex claim code, for an apply that starts Plex for the first time. The code has to look like one from plex.tv/claim, and it works for only 4 minutes. kia writes the request so only you can read it, and the service deletes its copy as soon as it has read it. A code that's used goes into `.env`, which only you can read, and the service never prints it.
- It runs `scripts/stack.sh check` or `apply` with those parts, and writes its answer, its list of containers, its own lock, and your `.env` copies only in its own folder or as new files.

So the most a taken-over kia can do through it is what you could do with `scripts/stack.sh apply` and the parts in [choosing the parts](docs/stack.md#the-parts): start or stop them. That includes `observability-disks`, which runs privileged (above). An apply from the Dashboard doesn't stop to ask about viewers the way the script does in a terminal; the Stack page asks instead. Without the service, nothing reads those requests.

`scripts/update.sh` also keeps what it needs between runs where kia can't write (in git's own folder), and checks it before use.

### The Dashboard

From the Dashboard you can change [profiles](docs/profiles.md), which playlists kia checks (including adding a playlist by its address), favorites and channel numbers, the password, and with the kia-stack service, `KIA_STACK`; you can also test a channel or take it out of the playlists. No other setting in `.env` or `kia.toml` can be changed there.

- Changes need the admin password and are accepted only on the secure (HTTPS) page, so the password isn't sent in the clear. kia believes that a request came through the secure page only from its own web server, which sets that on each request itself, not from other containers or the browser.
- kia keeps only a scrambled (scrypt) form of the password, and refuses requests from other websites.
- It slows down guessing: a second after each wrong password, and from the second one in a row, a wait that doubles each time, up to a minute, for the address that sent them. The web server passes on that address itself, so a browser can't claim someone else's, and guesses sent all at once take their turn like any others.
- Channel source addresses can hold a provider's user name and password; kia shows them in full only on the secure page and only after the password, and hides them everywhere else, **Check now**'s progress included.
- kia's own certificate is self-signed, so a browser can't tell it apart from a fake one the first time; on a home network that's a small risk, and [your own certificate](docs/secure-page.md#using-your-own-certificate) removes it.

### Pluto TV

The [Pluto TV relay](docs/how-it-works.md#pluto-tv) fetches only https addresses (redirects included), plays only the Pluto channels in kia's playlists, at most 20 at once, and downloads at most 16 MB for any one address, so a server that sends too much can't fill kia's memory.

### Published images

Published images are scanned before they're pushed, and carry an SBOM and a signed build attestation ([how to check one](docs/configuration.md#the-published-image)).

Keep kia on your home network. Don't open a port for it on your router; to reach it from outside, use a VPN such as Tailscale.

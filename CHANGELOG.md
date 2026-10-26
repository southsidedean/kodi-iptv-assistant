# Changelog

## 0.6.6 (2026-10-25)

### Upgrading from 0.6.5

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). If your assistant uses kia's MCP server, it picks up the new tools (`kia_pluto`, `kia_setup`, and `kia_find_shows`) the next time it connects. **Ask kia what's wrong** may now show a problem it didn't before, such as low disk space or a certificate of your own that's about to expire; `kia doctor setup` explains each.

### Asking kia why

- `kia doctor pluto` explains Pluto TV through kia's fix: how many channels use it, what's playing now and for how many devices, any channel playing Pluto's plain link instead (a problem after 5 minutes, when the alert goes off), each playing channel's last problems, and how often Pluto fell back in the last week. It replaces the overview's single Pluto note, and names the channel.
- `kia doctor setup` checks kia's own setup: your own certificate for the secure Dashboard expiring or expired (kia renews the one it made), a certificate and key that don't match, the first password kia made still in use, nowhere to send alerts, under 1 GB free for kia's folders, an address only this computer can use, and every playlist's streams failing at once (usually the network or ffprobe).
- **Ask kia what's wrong** includes the problems of both. Scripts can ask `GET /api/doctor/pluto` and `GET /api/doctor/setup`, and AI assistants get `kia_pluto` and `kia_setup` tools ([asking kia for help](docs/ask-kia.md)).

### Finding shows

- `kia find` searches the TV guides for shows: kia's guide, and the Live TV guides of the Plex and Jellyfin that kia knows, when they have guides of their own (an antenna's channels in Plex, for example). Give it words, a category such as Sports, or both, and how many hours ahead to look (`--hours 0` is on now, and the most is a week). It lists up to 25, the ones with your words in the title first, each with its channel, number, times, and where to watch it ([asking kia for help](docs/ask-kia.md)).
- AI assistants get the same as a `kia_find_shows` tool, so they can answer "when is the Bears game on, and on which channel?". `kia_whats_on` also covers Plex's own channels now, so "what's on WBBM?" works for an antenna's channels too.

## 0.6.5 (2026-10-25)

### Upgrading from 0.6.4

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). If your assistant uses kia's MCP server, it picks up the `kia_guide` tool the next time it connects.

### Asking kia why

- `kia doctor guide` explains the whole guide in one answer. It says when the guide was last rebuilt and how many TV channels have a full, partial, or no guide. It names the sites the guide comes from, and the sites on a streak of failed downloads, with their logs' last errors. It counts the channels that matched a guide site that sent nothing, and the channels that matched no site at all. **Ask kia what's wrong** on the Dashboard includes its problems (a guide that hasn't been rebuilt for over a day, a site that keeps failing), scripts can ask `GET /api/doctor/guide`, and AI assistants get a `kia_guide` tool ([the guide under the hood](docs/guide-under-the-hood.md#when-a-channels-guide-is-wrong-or-missing)).

## 0.6.4 (2026-10-25)

### Upgrading from 0.6.3

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). If your assistant uses kia's MCP server, it picks up the `kia_jellyfin` tool the next time it connects. For `kia doctor jellyfin`, kia needs Jellyfin's address and an API key in `.env` (`KIA_JELLYFIN_URL`, `KIA_JELLYFIN_TOKEN`), the same ones its Jellyfin dashboard uses.

### Asking kia why

- `kia doctor jellyfin` explains why Jellyfin shows no channels, or no guide. With kia's API key for Jellyfin (`KIA_JELLYFIN_TOKEN`), it asks Jellyfin which playlist and guide its Live TV reads and checks them: whether they're kia's, whether the playlist has radio stations (which Jellyfin numbers apart from TV), and whether the guide goes with it. It also checks how many channels Jellyfin shows and how its last guide refresh went. **Ask kia what's wrong** on the Dashboard includes its problems, scripts can ask `GET /api/doctor/jellyfin`, and AI assistants get a `kia_jellyfin` tool ([Jellyfin](docs/jellyfin.md#if-somethings-wrong)).
- `kia doctor plex` (and so **Ask kia what's wrong**) no longer reports problems with a Plex that's gone. After a switch from Plex to Jellyfin, the Threadfin folder and `KIA_PLEX_URL` stay behind, and it took them for a Plex that kia feeds. It now goes by kia's last check of the stack, and says when `KIA_PLEX_URL` names a Plex that isn't in it. `kia doctor jellyfin` does the same for `KIA_JELLYFIN_URL`.
- `scripts/stack.sh apply` (and **Apply** on the Stack page) now empties `KIA_PLEX_URL` or `KIA_JELLYFIN_URL` when Plex or Jellyfin leaves the stack, if it still holds the container's own address. Choosing the part again sets it back. An address that points anywhere else stays, and so do the tokens.

## 0.6.3 (2026-10-25)

### Upgrading from 0.6.2

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). If your assistant uses kia's MCP server, it picks up the `kia_hardware` tool the next time it connects. `kia doctor hardware` needs the observability part; without it, it says so.

### Asking kia why

- `kia doctor hardware` checks the computer kia runs on, from the numbers the stack's Prometheus keeps for Grafana's Hardware dashboard: disks that are nearly full, software RAID with a failed disk, memory errors, the processor slowing down for heat, and running on a battery. With their exporters, it also checks each disk's health (SMART: failing its own check, sectors it couldn't read or has replaced, heat, an SSD's wear) and the graphics card's temperature. **Ask kia what's wrong** on the Dashboard includes its problems when Prometheus is in the stack, scripts can ask `GET /api/doctor/hardware`, and AI assistants get a `kia_hardware` tool ([observability](docs/cookbooks/observability.md#if-somethings-wrong)).
- On a Mac, Time Machine's snapshots of the main disk no longer show as disks of their own in the Hardware dashboard's free space.

## 0.6.2 (2026-10-25)

### Upgrading from 0.6.1

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). With `KIA_PLEX_URL` set, `kia doctor plex` reports what kia found in Plex after the next refresh; until then it says kia hasn't added the tuners yet. To see it sooner, use **Add Live TV to Plex** on the Stack page.

### Asking kia why

- `kia doctor plex` explains why Plex shows no channels, or too few. It checks each Threadfin (does it answer, which kia playlist it reads, whether that playlist exists and fits Plex's 480 channels per tuner), what kia found the last time it added its tuners to Plex, and, with Plex in kia's stack, whether Plex has the codecs Live TV needs. **Ask kia what's wrong** on the Dashboard now includes Plex's problems, scripts can ask `GET /api/doctor/plex`, and AI assistants get a `kia_plex` tool ([Plex](docs/plex.md#if-somethings-wrong)).
- `kia plex-tuners` now saves what it found (`state/plex-tuners.json`), so these answers work without asking Plex again.

## 0.6.1 (2026-10-25)

### Upgrading from 0.6.0

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). If your assistant already uses kia's MCP server, it picks up the two new tools and the prompts the next time it connects; nothing to change. To set up another assistant, see [kia and AI assistants](docs/mcp.md).

### AI assistants

- [kia and AI assistants](docs/mcp.md), a full guide to kia's MCP server: which assistants can use it and which can't (assistants in a company's cloud, such as ChatGPT and claude.ai, can't reach kia at home), setup for Claude Code, Gemini CLI, Claude Desktop, Codex, VS Code, and Cursor, trusting kia's certificate, what the assistant sees, privacy and safety, a table of errors and fixes, and a reference for developers.
- `kia mcp-token --for <assistant>` prints the setup for `claude-code` (the default), `claude-desktop`, `gemini`, `codex`, `vscode`, `cursor`, or `curl`, with your address and token filled in.
- Two more tools: `kia_whats_on` (what's on a channel now and next) and `kia_docs` (searches the docs installed with your kia, so an assistant can give kia's own steps and links). And two starter prompts, **kia checkup** and **explain a channel**, which Claude Code offers as slash commands.
- `kia mcp` runs the MCP server over standard input and output, for an assistant on the same computer as kia, which starts it itself: no certificate, token, or network. With Docker: `docker compose exec -T kia python3 -m kia mcp`.

## 0.6.0 (2026-10-25)

### Upgrading from 0.5.7

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). The MCP server stays off until you run `kia mcp-token`. Until the first guide build after the update (within a few hours), **Why?** says a channel's guide sources are known but not which one it uses.

### Asking kia why

- `kia doctor` explains what's wrong, using what kia has already written down: a failed refresh, its alerts, playlists whose streams all fail, a guide that's mostly empty, parts of the stack that don't answer. `kia doctor channel <name>` covers one channel: whether its stream works and how it has done, or why it was left out. It also says where the channel's guide comes from, or why it has none, and quotes the guide site's log when the site is the cause. Each answer names the file it comes from and what to do. `--json` gives the same for scripts. It changes nothing and works offline ([troubleshooting](docs/troubleshooting.md)).
- The same answers on the Dashboard: **Ask kia what's wrong** on the main page, and **Why?** next to each channel on a playlist's page. Both only read, so they don't need the password. Scripts can ask `GET /api/doctor` and `GET /api/doctor/channel?q=` ([API](docs/api.md)).
- kia has an MCP server, so an AI assistant such as Claude Code can answer questions about your setup from kia's record. It's off until you make its token with `kia mcp-token`, then answers only over HTTPS with that token, at `/api/mcp` on the secure port. Its six tools only read: the overview, one channel, finding channels, the playlists, the stack, and the settings. Secrets show only as set or not set, and logins in addresses are hidden. A wrong token is slowed down like a wrong password, the token is left out of backups, and kia's log records each call. [Asking kia for help](docs/ask-kia.md) has the setup, including trusting kia's certificate, and what to keep in mind when the assistant can also run commands.

## 0.5.7 (2026-10-25)

### Upgrading from 0.5.6

Update with `scripts/update.sh` (without Docker, `scripts/update.sh --no-docker`). There's nothing else to do.

### Choosing the stack

- Each preset says what it will ask of you before you choose it: Plex's playlist and claim code, your media folder, Jellyfin's API key, or your own Plex's address and token. The Stack page's cards, the installer's list, and `scripts/stack.sh list` show it, and the presets table in [choosing the parts](docs/stack.md#presets) has it too. A card's text now stays at its top, however tall the cards beside it are.

### Running without Docker

- On Linux, the kia service gets systemd's limits: only the system calls an ordinary service makes, and the computer and your home folder read-only except kia's own folders and npm's cache. On Ubuntu 23.10 and newer, a service you run as yourself can't have the file limits (AppArmor won't allow it), and `scripts/install-service.sh` says so; the system call limits still apply. `scripts/update.sh --no-docker` puts the new service file in place. If you moved one of kia's folders in `kia.toml`, see [without Docker](docs/setups.md#without-docker).

### Docs

- Four pages under the hood, each with a diagram: [how the guide gets built](docs/guide-under-the-hood.md), [the Pluto TV relay](docs/pluto-under-the-hood.md), [Plex and Threadfin](docs/plex-under-the-hood.md), and [security](docs/security-under-the-hood.md).
- [Using kia from scripts](docs/api.md) lists every `kia` command, and the fields `GET /api/sources` and `GET /api/stack` gained. [Configuration](docs/configuration.md) lists the last few `kia.toml` settings it was missing.
- Two corrections: Threadfin reads kia's playlist every 6 hours, not nightly, and a Pluto TV channel can pause for up to 45 seconds when Pluto goes back, not half a minute.

### Maintenance

- The graphics card's exporter (`observability-gpu`) moves from `4.3.1-4.4.1-ubuntu22.04` to NVIDIA's newest, `4.6.1-4.8.4`. It was tested on an NVIDIA card with the Hardware dashboard, where all of its panels have data. A `KIA_DCGM_IMAGE` of your own in `.env` still wins.
- A weekly check says when an image `compose.yaml` pins to a tested version (Threadfin, the graphics card's exporter) has a newer release of the same kind (`scripts/pinned-images.py`).
- For contributors: each test has a one-minute limit, so one that hangs fails by name, and the macOS tests are now required for every change.

## 0.5.6 (2026-10-24)

### Upgrading from 0.5.5

Update with `scripts/update.sh`. To choose what runs from the Dashboard's new Stack page, install the kia-stack service once, in the kia folder: `scripts/install-service.sh --stack` ([your stack from the Dashboard](docs/stack-dashboard.md)). On Linux, also run `sudo loginctl enable-linger "$(id -un)"` once, so it keeps running after you log out.

If `KIA_STACK` is empty and you set `COMPOSE_PROFILES` by hand, nothing changes: `COMPOSE_PROFILES` still decides. To use the Stack page, move to `KIA_STACK` first, using [the table](docs/stack.md#without-kia_stack). The profile `plex` (Threadfin only) is the part `plex-tuner`, not `plex`.

Three changes can need a step of your own:

- kia checks the numbers and patterns in `kia.toml` when it starts. A value 0.5.5 took, such as `days = "3"` (a number in quotes) or `timeout = 0`, now stops kia with one line naming it, in `docker compose logs kia`. Fix the line, then `docker compose up -d`.
- kia no longer tests streams on your own network that come from a playlist from the internet ([why](SECURITY.md#playlists-from-the-internet)), so if one of those lists pointed at your network, its channels drop out. Your own playlist files, and playlists downloaded from an address on your network, aren't affected. For a list from elsewhere whose streams really are on your network, add the word `lan` after its name in your sources file.
- The installer no longer opens kia's ports in ufw, since connections to Docker's ports get past ufw on their own. Rules an earlier install added can go: `sudo ufw delete allow 8780/tcp` and `sudo ufw delete allow 8743/tcp`.

### Changing the stack from the Dashboard

- A new **Stack** page on the Dashboard shows what runs next to kia, each program's state, and on the secure page lets you choose what runs: tick the parts or start from a preset, **Check**, then **Apply**. Apply comes on only after a clean Check of the same choice, and asks first if someone's watching or if parts would stop. Without the kia-stack service, the page shows the command to run instead. `scripts/stack.sh check` now also says which running parts a choice would stop.
- The Stack page shows the presets as cards, each with what it's for and what it needs, with the parts one by one below for a combination of your own. Two new presets: `plex-box-graphs` and `jellyfin-box-graphs`.
- For Plex's first start, the Stack page asks for the claim code (`scripts/stack.sh apply --claim CODE` in a terminal), and offers to make the `plex` playlist when the check says it's missing. After Apply, it says what each new part needs next, with a button that adds Live TV to Plex now instead of within 4 hours.
- `scripts/stack.sh apply` fills in `KIA_PLEX_ADVERTISE` and `KIA_JELLYFIN_ADVERTISE` from `KIA_PUBLIC_URL` when they're empty.
- The installer asks whether to install the kia-stack service, and installs it unless you say no (`--no-stack-service`).
- A new service, **kia-stack**, lets the Dashboard's Stack page change what runs next to kia. The kia container can't reach Docker, on purpose, so the service runs `scripts/stack.sh watch` on the computer as you, carrying out the check or apply the page asks for. Install it with `scripts/install-service.sh --stack`, or `--stack-service` when you run the installer ([how it works](docs/stack.md#changing-the-stack-from-the-dashboard)). It takes only `check` or `apply` with part names it knows, and refuses anything else. It's built for a kia container that has been taken over: it never writes, reads, or follows anything in `state/` (where kia leaves the request), keeps its own files in `stack-service/`, which kia can only read, and removes anything that isn't a plain request unread ([security](SECURITY.md#the-kia-stack-service)). `scripts/update.sh` restarts it when its script changed.
- `scripts/stack.sh check --stack plex,observability` checks a choice without changing `.env`, and `apply --stack` sets `KIA_STACK` first.

### The Dashboard's stack tiles

- With Docker, the Dashboard starts with **Your stack**: a tile for each program running next to kia (Threadfin, Plex, Jellyfin, Prometheus, Grafana, and the exporters), green when it answers and red when it doesn't. kia checks each one every minute, and a tile with a page of its own (Plex, Jellyfin, Threadfin, Grafana) opens it. The same is in `stack.json` and in `/metrics` as `kia_stack_service_up` ([how it works](docs/how-it-works.md#the-dashboard)).

### Choosing the parts of the stack

- An empty `KIA_STACK=` line, which every `.env` made from `.env.example` has, counted as a choice of nothing. So with Threadfin started the way [Plex](docs/plex.md) said before, with `COMPOSE_PROFILES=plex`, `scripts/stack.sh` said nothing else ran, and `apply` stopped Threadfin (after asking, or at once with `--yes`). Now, while `KIA_STACK` is empty and `COMPOSE_PROFILES` isn't, `COMPOSE_PROFILES` decides, and `check` and `apply` change nothing. The Plex guide now uses `KIA_STACK=plex-tuner` and `scripts/stack.sh apply`.
- `apply` starts the profiles it chose even when `COMPOSE_PROFILES` is also set in your shell, which Docker Compose would have used instead.
- Ports in use are found with `ss` when `nc` isn't installed, and `check` says when neither is.

### Channel sources

- More to choose from in `sources.txt` and on the Sources page, all off until you turn them on. TV: the free channels on TCL's TVs, TCL+, and MetaX, which add about 390 working channels kia didn't offer (tested October 2026; no guide covers them). Radio: Radio Paradise, WFMU, KCRW, KUTX, Nightride FM, NTS, and FIP, through radio-browser.

### The Dashboard

- Saving a change on the Dashboard waited while the nightly check looked up Pluto TV channels in other services' lists, a request each, which could take minutes. kia looks them up first now, so the Dashboard waits only for kia to write its files.
- The "Channel names containing" boxes are patterns, so `+1` or `A&E?` didn't do what they say. The page says so now, and a pattern kia can't use is explained in plain words, with how to match the text as written.
- With `channel_numbers = false` in `kia.toml`, the Dashboard still offered to change numbers and Renumber everything, which did nothing. Those are hidden then, and the API says why. Letters typed in a number box no longer clear the channel's number.
- The Test button tests a channel the way the nightly check does, with `[check]`'s `require_video`, `user_agent`, and `ffprobe`.
- For a playlist whose address has a login in it, a running Check now lost its progress and its Stop button when the page was drawn again (after adding a source, say). It keeps them now.
- The Last refresh tile on the kia dashboard showed nothing during a refresh, instead of Refreshing.
- After a change on the Dashboard, the System page showed the settings kia started with, even after `kia.toml` had changed.
- With the right password during a wait after wrong ones, the Sources page said "Wrong password." It now says what kia said: how long to wait.
- Counting a playlist's channels by name sent the password box's contents to kia with each change, so a mistyped password added up to many wrong ones and a wait for everyone. Counts now use only a password kia has already taken.
- A stream check that kia's restart cut off showed "0 streams: 0 working, 0 flaky, 0 dead". It now says kia restarted, so the check stopped and nothing changed.
- While kia restarts, the System page said "Editing isn't available from this server", as if for good, and the Sources page stayed blank. Both now say kia didn't answer just now.
- An empty Current password box on the System page said "That's not the current password." It now asks for the password.
- Screen readers now read the System page's messages and the note after saving a playlist: each is on the page before its text arrives. The Sources page reads "Canada, 3 of 5 on", not "Canada3 of 5 on".
- With an IPv6 address in `KIA_PUBLIC_URL`, kia made a new certificate at every start, and the links to the secure page and Grafana didn't work (`https://fd00::5:8743/`). They're `https://[fd00::5]:8743/` now, and the certificate stays.
- A playlist made or renamed on the Dashboard while a kia command ran by hand could lose its page until the next update: each rewrote the pages from what it had read. They now take turns.
- A setting kia can't read, such as `KIA_RUN_AT=4am` or `KIA_EPG_DAYS=abc`, stops kia with one line naming it, instead of a Python traceback. A browser turning down kia's own certificate, or http:// sent to the secure port, is one line in the log too.
- That goes for `KIA_PORT`, `KIA_HTTPS_PORT`, `KIA_API_PORT`, `KIA_GUIDE_REFRESH_HOURS`, and `KIA_RUN_ON_START` too, and for a missing `kia.toml`. kia also checks the numbers and patterns in `kia.toml` when it starts: `days = "3"`, `timeout = 0`, or an `exclude_name_patterns` entry that isn't a valid pattern. A bad pattern used to stop every stream check, and a bad number stopped a refresh part way through.
- The System page and `kia settings` show `KIA_STACK`, the parts of the stack.
- `kia serve` (installs without Docker) takes turns with a refresh run from another process, like the Docker setup.
- Two writers of the same file at once (the daemon and a kia command, or Plex's and Jellyfin's numbers) could fail or lose a change; each now writes its own temporary file.

### Plex

- With Plex in the stack and two Threadfins, `kia plex-tuners` never added the second Threadfin's guide to the DVR, so its channels got the first one's guide or none. In the stack both Threadfins use port 34400, and kia told guides apart by port. It now tells them apart by name there, and by port for Threadfins on another computer.
- When a new Plex in the stack has no codecs yet, kia tries the warm-up once a day after refreshes, instead of after every guide refresh: one try can take 20 minutes while Plex scans its libraries. `kia plex-tuners` run by hand still tries every time.
- `kia plex-tuners --remove` found kia's Threadfins by asking Plex to add each one as a tuner, which added any that Plex didn't have yet. It now looks in the DVR's own list of tuners. The ffmpeg that makes the warm-up clip gets none of kia's passwords or tokens, like every other one kia runs.
- When Plex's warm-up failed and removing its library failed too, the second error hid the first, and the clip stayed in `plex/warmup`. The clip goes first now, and the reason for the failure is kept.

### The guide

- A refresh just after midnight UTC (evening in the Americas) left out the show on air on i.mjh.nz channels (Pluto TV, Plex, Samsung TV Plus, Roku) and in guides named by playlists, until the next one began. Shows still on at the start of the day count now.
- `site_time_limit` now applies to those two kinds of guide too, as the docs said, not only to iptv-org's sites.
- When one of i.mjh.nz's files fails tonight (Plex's, say), its channels keep the programmes still to come from last night's guide, instead of having none until tomorrow. A guide cut off half-way adds nothing.
- The same now goes for guides named by playlists: when one of a playlist's guides fails and others work, the channels that got nothing keep what's still to come from last time, and the quick refreshes keep trying the one that failed. Before, those channels had no guide until a nightly refresh found it working again.
- An i.mjh.nz file cut off mid-download, or with damaged compression, failed every i.mjh.nz channel instead of only that file's.
- A guide taken from another kia server (`KIA_UPSTREAM_URL`) is read in full before it replaces the last copy, so a broken one no longer turns this computer's guide into channel names only.
- Reading a big guide left an empty element behind for each programme, about 24 MB for 300,000 of them. Now kia keeps nothing it has already read.
- A first download of iptv-org/epg that failed (no internet at the first start) left an empty copy that `kia map` and `kia grab` then took as done, without `--update`. kia now finishes the download the next time.
- A missing `docker` or `npx` for one guide site stopped the whole download. Only that site fails now.
- Guide times written with no space before the offset (`20261024120000-0500`) were read as UTC.
- A channel name's `(1080i)` comes off in the guide's channel name too, as `(720p)` always did.
- The DNS check asks the next public resolver when one fails (SERVFAIL), instead of saying the site has no address, and asks a resolver given as an IPv6 address over IPv6.
- A bad `min_bitrate` on a `radio-browser:` line is reported when kia reads `sources.txt`.
- A radio-browser or SomaFM answer in an unexpected shape (an error message, a number where a name belongs) failed the whole stream check. Stations kia can't read are skipped now, and a list kia can't read is reported as that source failing. Half an emoji in a station name (an unpaired surrogate) no longer fails the check either.

### Observability

- Tiles that count something (who's watching on Plex or Jellyfin, video converted on the card or the processor, failed RAID disks, memory errors, throttling) showed 0 when their source wasn't set up at all. They now show their note, such as "kia isn't set up to ask Plex", and 0 only when there's really none.
- The Hardware dashboard's *Running on* tile could never say Battery on Linux. It now reads whether the mains supply is online.
- The Pluto TV panels on the kia dashboard say the relay is off (`KIA_PLUTO_RELAY=false`) instead of "No numbers from kia yet".
- `/metrics` asks Plex and Jellyfin at the same time. When neither answered, asking one after the other took longer than Prometheus waits, and every kia number was lost from that scrape.
- With Plex in the stack claimed after kia started, the Plex dashboard said "Not answering" until kia restarted: kia read Plex's sign-in only at its start. It reads it again now while Plex turns kia away, and kia's log says when Plex or Jellyfin stops answering.
- Two players that look exactly the same (no name, the same app) were two identical lines in `/metrics`, which Prometheus drops. They're one line now, with 2 as its value.
- A Jellyfin dashboard in Grafana, like Plex's: whether Jellyfin answers, what's playing now on each player (by pseudonym), live TV against your own media, copied or converted video, players away from home, and what Jellyfin in the stack sends. It needs `KIA_JELLYFIN_URL` and `KIA_JELLYFIN_TOKEN`, the same settings as Jellyfin's numbers in `/metrics` ([graphs for your TV stack](docs/cookbooks/observability.md#5-jellyfin-optional)). `.env.example` already promised it.
- The Hardware dashboard's *Converting on the card* and *Converting on the processor* tiles count Jellyfin's sessions as well as Plex's.

### Installing

- The `plex-elsewhere` preset stopped the installer before kia started: Threadfin needs kia's `plex` playlist, which you make on the Dashboard after the first refresh. With either Plex preset, the installer now starts kia and lists what's next in order: make the playlist, then (for Plex in the stack) get the claim code, then run `scripts/stack.sh apply`. It used to say to get the claim code first, and it ran out while `apply` waited for the playlist.
- The installer checks that Docker is running, not only installed, so it stops with what to do (start Docker Desktop, or log out and back in after joining the docker group) before downloading anything.
- Without a terminal (`ssh host 'curl … | sh'`, cron), it stopped at its first question. It now takes the defaults, as with `--yes`.
- It finds this computer's address from the connection to the internet (`en5` on a Mac with a USB network adapter, say), and says so when it can't. An address typed with `http://` or a port is cleaned up, and one with characters an address can't have, or a time zone that isn't one, stops it before `.env` is written. It used to leave an empty `.env` behind, which the next run kept.
- An option without its value (`--dir` at the end) says so, instead of a shell error.
- Piped from curl, the installer is main's copy while the kia it downloads is a release. It now hands over to that release's own installer, with the same options, so the installer and the files come from the same version.
- With a preset, the Plex and Jellyfin addresses came out wrong when `KIA_PUBLIC_URL` had `https://`, a slash at the end, or no address at all. They're read the way `stack.sh` reads them now, and left empty when there's nothing to use. An IPv6 address gets its brackets (`http://[fd00::5]:8780`).
- The installer no longer opens ports in ufw. Docker lets connections to its ports past ufw by itself, so those rules opened nothing and could pass for protection. It says that `KIA_BIND` decides who can connect, and the docs say the same.
- On a Mac with less than 6 GB of memory, it uses the lighter settings too, and the firewall step opens the ports in `.env` rather than the default ones.

### Updating

- `scripts/update.sh` asks before restarting while someone watches through Jellyfin, not only Plex and Pluto TV.
- A copy that follows main (`update.sh --main`) stays on main when you run `update.sh` again. It used to move back to the last release without saying so; name a release to go back to one.
- When kia runs but its numbers don't answer, `update.sh` and `stack.sh apply` ask before restarting, instead of taking it as nobody watching. When kia isn't running, they go ahead.
- `KIA_IMAGE` pinned by digest (`...@sha256:...`), or on a registry with a port (`my-server.lan:5000/kia`), is left as you set it. `update.sh` used to rewrite either into a name that doesn't exist.
- `scripts/pin-versions.sh` pinned nothing for the base image when Docker hadn't kept it, which is usual. It now pins today's newest by digest, and says so.
- When an update stopped after moving the files and before restarting (Docker not running, a failed build), the next run forgot that the web server's or Prometheus's settings had changed. It now keeps what the containers last started with, in git's own folder (`kia-deployed-rev`).
- With a published image of one version (`KIA_IMAGE=…:0.5.4`), `update.sh` moved the files to the new release but pulled the old image again. It now sets `KIA_IMAGE` to the new release's version.

### Security

- A playlist from the internet can no longer have kia's nightly check send requests to your own network: your router, a printer, or the other containers of kia's stack. kia tests a stream from such a playlist only when its address is on the internet, and the report says why it left one out. The guides the playlist names for itself are held to the same rule. Your own playlists (files on this computer, or downloaded from an address on your network, such as a tuner or Threadfin) aren't affected, and the word `lan` on a playlist's line in the sources file allows it for one from elsewhere. [Security](SECURITY.md#playlists-from-the-internet) says what this does and doesn't stop.
- `scripts/update.sh` kept what the containers last started with in `state/deployed-rev`, which the kia container can write, and passed it to git as it was. A kia container that had been taken over could have written an option there that emptied any file of yours on the next update. It's kept in git's own folder now, and used only if it's a commit; an old copy in `state/` is taken over once, if it is one, and removed.
- `scripts/stack.sh apply --preset` wrote the preset's `KIA_STACK` to `.env` before checking it, so a refused or cancelled apply still changed it, with no copy of `.env` kept. `.env` changes now only once the check has passed and the questions are answered, and the copy of `.env` is made as a new file, never written through something already at that name.
- The Pluto TV relay downloads at most 16 MB for any one address, so a server that sends far more (a broken one, or a hostile one reached through Pluto's redirects) can't fill kia's memory. A request for a piece of a channel that isn't playing no longer holds one of the 20 places until it times out.
- Before updating its copy of iptv-org/epg, kia rewrites that copy's git settings from scratch. With `runner = "docker"`, iptv-org's code could otherwise have left a setting there that git runs as a command on the next update. kia also compiles iptv-org's scripts only with its own fixed esbuild now, never one from iptv-org's dependencies, since compiling runs outside Node's sandbox.
- Grafana's login cookie is marked Secure and SameSite. Browsers send a cookie to every port of a computer, so before this, opening kia's plain-http Dashboard after signing in to Grafana sent Grafana's session across your network unencrypted.
- Wrong passwords on the secure Dashboard slow down only the address that sends them: from the second one in a row, the wait doubles each time, up to a minute. Before, one person guessing held up everyone, the right password included, a second per guess. nginx passes on that address itself, so a browser can't name another one, and wrong passwords sent all at once take their turn and count one by one. An API address made to reach kia through another of nginx's locations is refused. An empty password box says "Enter kia's password first." instead of "Wrong password."
- A password with accents or emoji locked you out of changes, because browsers can't send those characters, and every page said kia didn't answer. kia now refuses such a password when you set it, and says at start when `KIA_ADMIN_PASSWORD` has one or is shorter than 8 characters.
- The other programs' data in the kia folder (Jellyfin's users, Grafana's database and password) is hidden from kia, and ffprobe and ffmpeg, which open streams from the internet, run with none of kia's passwords or tokens.
- kia's pages say not to load anything from other sites (Content-Security-Policy), not to guess file types, and not to send where you came from. Channel names and shows from other people's playlists were already escaped; this is a second line.
- `kia restore` keeps a sources file of your own in its safety copy, so a restore can be undone, and writes restored files privately from the start.
- The installer and `scripts/install-service.sh` print the Dashboard password only when kia has just made it; after that, they say where it is. `install-service.sh --dry-run` hides passwords and tokens.
- The arm64 image (Raspberry Pi) is scanned before it's published too, not only the amd64 one.
- The image published is built on the same base image the scans saw, pinned by its digest for the whole run. Before, the final build fetched the base again, and a new one could arrive in between.
- `threadfin-setup` gets the same view of the kia folder as kia, without Jellyfin's and Grafana's data. Comments in `compose.yaml` and the docs no longer say kia can't read `.env`'s passwords (it can, for backups) or that the web server sees only `output/`.
- A `$` in a `.env` value is read by Docker as another setting: `KIA_ALERT_COMMAND` lost `$KIA_ALERT_MESSAGE`, and a password with a `$` was cut short. `.env.example` and the alerts docs say to put such a value in single quotes, and kia without Docker on a Mac now takes the quotes off, as Docker and systemd do.
- [SECURITY.md](SECURITY.md) now covers the web server's privileges, Threadfin's and Grafana's passwords, and Jellyfin's setup screens, which are open to anyone on your network until you finish them (the [Jellyfin cookbook](docs/cookbooks/jellyfin-box.md) says so too).

### Docs

- More pictures where people get stuck: what the installer prints at the end, the Dashboard after the first refresh, Chrome's certificate warning for the secure page, and the Sources page. A flowchart at the top of [troubleshooting](docs/troubleshooting.md) points to the right section, and [the stack under the hood](docs/stack-under-the-hood.md) has a diagram of a request from click to answer.
- [Your stack from the Dashboard](docs/stack-dashboard.md) walks through choosing Plex, Jellyfin, or the graphs on the Stack page the first time, from installing the kia-stack service to finishing each part.
- [The stack under the hood](docs/stack-under-the-hood.md) shows how it works: parts and Compose profiles, what check and apply do step by step, and how the Stack page and the kia-stack service pass requests and answers.
- [Choosing the parts](docs/stack.md#without-kia_stack) has a table for moving from `COMPOSE_PROFILES` to `KIA_STACK`. The observability cookbook's example no longer suggests `plex` to someone whose `plex` profile meant Threadfin only, which would have started a second Plex.
- The API docs give the right answers to a missing or wrong password (`401`, then `429` while kia makes an address wait). The certificate steps run from cron or a certbot hook give the files to kia's user, not root. Removing kia removes the kia-stack service too.
- New diagrams for the kia-stack service and the Jellyfin box, and the Pluto TV diagram says kia fetches each piece when a player asks for it.
- The README's manual install moves the copy to kia's newest release and asks for `PUID` and `PGID`, as [installing kia](docs/install.md) does.
- "Channel sources" for the playlists kia reads, and "playlists" for the ones it makes, on the first pages a new user reads.
- A Raspberry Pi, or any computer with less than 6 GB of memory, changes the three lighter settings already in `.env`. Adding them near the top did nothing, because the empty lines further down win.
- Changes to `profiles.toml` show up at the next guide refresh, within 4 hours, or now with `kia refresh-guide`; the docs said to run the hour-long stream check.
- [Metrics](docs/metrics.md) says `/metrics` shows what's playing on Plex and Jellyfin, by pseudonym, to anyone on your network.
- Smaller corrections: a removed channel comes back after three good nights; Start here checks the Dashboard before setting up devices; Kodi's playlist address works once the first refresh is done; `observability` includes cAdvisor; the image versions you can pin include Jellyfin's and the disk exporter's; the Plex box cookbook works on macOS too.
- The Kodi screenshots no longer carry the display's color profile, and the TV stack screenshot shows the current dashboards.
- The Plex screenshots show only Plex's own Tuner Setup and Live TV pages, the playlist page and the Grafana kia dashboard show the current menus and buttons, and no screenshot carries camera or display data any more.

### Smaller fixes

- A channel source that answers with an error page or an empty file counts as a failed download now. kia keeps using its last good copy, up to 7 days old, instead of saving the error page over it and losing that source's channels.
- `kia backup` works when `kia.toml` is broken, as it says it does, and `kia restore` works when `kia.toml` is missing, which is when you need it. A restore leaves a linked certificate (`tls/cert.pem` linked to Let's Encrypt's, say) as it is, so renewals keep reaching kia.
- A damaged `state/history.json` stopped every stream check. kia now keeps it as `history.json.bad` and starts a new one.
- The second, slower try for streams that timed out tests no more at once than `KIA_CHECK_CONCURRENCY`.
- A pinned or chosen channel number above the automatic ones could go to a new channel after its own channel had been gone for a while. Numbers aren't reused unless you turn on recycling.
- An attribute with a namespace in a guide site's channel list (`xml:lang`) made kia write a channel file iptv-org's grabber couldn't read.
- `kia.toml` lists KEXP with the guides kia reads itself, and the profiles example lists `exclude_channels` and `threadfin`, and says when changes show up.
- `kia_plex_up` and `kia_jellyfin_up` said 0 when kia couldn't write its own list of players in `state/`, though the server was fine. The sessions still count then, with no pseudonym.
- If kia stopped half-way through giving Threadfin its login, Threadfin's page stayed open; kia finishes it on the next start. A damaged Threadfin `settings.json` no longer stops kia's setup.
- The Pluto TV relay no longer waits to try an address again that it won't fetch (not https).
- When Pluto gave a session of less than half an hour, the relay opened a new one at every clip change. It now renews in the session's last quarter. A channel watched for days no longer keeps a growing list of every player and clip it has seen.
- A `|` in a channel's group or id no longer breaks the table in `report.md`.
- `scripts/nfs-media.sh` read a dot in a share's name as any character, so `movies.4k` also matched `movies-4k`'s lines.
- `kia check-dashboards` (and `scripts/stack.sh status`) said Prometheus didn't answer when it turned down a single query. That panel now counts as empty and the rest are still checked.
- A playlist name, or a guide site's name, with a line break at its end passed kia's check, since `$` in a pattern matches before one.
- On the secure Sources page, the password box was unstyled and tiny, with its label and hint run together.
- Writing each playlist's guide left guide.xml open until Python tidied up, and error pages from Plex, Jellyfin, another kia, and channel sources were never closed. Both are closed when kia is done with them.

## 0.5.5 (2026-10-24)

### Upgrading from 0.5.4

Update with `scripts/update.sh`. With the `monitoring` profile on, Grafana's address changes from `http://` to `https://` (same port, 8788); your browser warns about kia's certificate the first time. Bookmarks need the new address; the Dashboard's **Observability** button has it.

0.5.4's `scripts/update.sh` doesn't yet restart Prometheus and the web server when their settings change (see *Observability*), so after this update run `docker compose restart prometheus` once if the `monitoring` profile is on. The web server's settings didn't change. Later updates do it for you.

### Choosing the parts of the stack

- New `scripts/stack.sh` ([choosing the parts](docs/stack.md)): one line in `.env`, such as `KIA_STACK=plex,observability`, says which programs run next to kia (`plex`, `plex-tuner`, `plex-tuner-2`, `jellyfin`, `observability`, `observability-gpu`, `observability-disks`). `scripts/stack.sh check` checks the choice for this computer: names it doesn't know, parts that don't go together, a Mac's limits, NVIDIA's toolkit for the graphics card, missing settings, Threadfin's playlist (Threadfin stops and restarts over and over without one), the media folder, ports in use, and memory. `apply` then sets `COMPOSE_PROFILES`, starts what's chosen, and stops what isn't, after asking if someone is watching or if parts that run now would stop. On its own, it shows what runs and which dashboard panels have data. The Plex and Jellyfin TV box cookbooks use it, and [presets](docs/stack.md#presets) cover the usual setups (`scripts/stack.sh apply --preset plex-box`).
- The installer asks what this kia is for and sets `KIA_STACK` from that preset (`kodi` unless you choose another), or takes `--preset NAME`. It then starts the preset's parts once, and its next steps cover only what it started.
- New `kia check-dashboards`: which Grafana panels have data on this computer, by dashboard section, and why the others don't.
- `COMPOSE_PROFILES` works as before for installs without a `KIA_STACK` line.

### Observability

- Grafana answers only over HTTPS, with kia's own certificate from `tls/` (the secure Dashboard's, or yours if you put one there), so its password no longer crosses your network in plain text. Grafana checks the certificate files every minute, so kia's renewals and your own certificate need no restart. The Dashboard's **Observability** button opens the `https://` address.
- After an update, Prometheus and the web server kept the settings they started with. Docker keeps a settings file mounted on its own pinned to the copy the container started with, and `git` replaces the file. So 0.5.4's new `gpu` and `disks` jobs never reached Prometheus on updated installs. `scripts/update.sh` now restarts whichever of the two had its settings changed by the update. If you updated to 0.5.4 another way, run `docker compose restart prometheus web` once.
- A dashboard panel with nothing to show says why, instead of Grafana's *No data*: *Needs observability-gpu in KIA_STACK, or the monitoring-gpu profile (an NVIDIA card, on Linux)*, *No UPS*, *No sensors to read here (macOS and virtual machines report none)*, *Nothing played in this time*, and so on. Every panel has one, and a test keeps it that way.
- On a new Docker install on Linux, which keeps images in containerd, the TV stack dashboard showed no containers: cAdvisor found them only through containerd, which it wasn't given. cAdvisor now reads containerd's socket too (Docker's `moby` namespace), and Prometheus keeps the two container labels the dashboards use. Older Docker installs and Macs work as before. This was a known problem in 0.5.4.

## 0.5.4 (2026-10-24)

### Upgrading from 0.5.3

Update with `scripts/update.sh` when nobody is watching: kia restarts, and with the `monitoring` profile on, so does Grafana, which brings back its dashboards (see *Observability*). Nothing else to do on Linux.

On a Mac with Plex in the stack, see [on a Mac](docs/configuration.md#on-a-mac): Plex there keeps its data in `plex/config`, and you can move it to a Docker volume.

### Jellyfin

- New, in beta: the `jellyfin-server` profile, Jellyfin in kia's stack, for a home TV box on free software ([Jellyfin TV box](docs/cookbooks/jellyfin-box.md)). It's tested from start to finish on Linux and a Mac in a web browser, but not yet with Jellyfin's TV and phone apps. Jellyfin reads kia's playlist and guide from the web server directly, with no Threadfin and no channel limit. It runs as your user, with a read-only system and no capabilities; your media folder is read-only to it. On a Mac, the installer keeps its database in a Docker volume, like Plex's. New settings: `KIA_JELLYFIN_ADVERTISE`, `KIA_JELLYFIN_PORT`, `KIA_JELLYFIN_NAME` (the server's name, `kia-tv-box` unless you change it), `KIA_JELLYFIN_IMAGE`, `KIA_JELLYFIN_CONFIG`, and `KIA_JELLYFIN_CACHE`.
- kia's metrics count what Jellyfin plays, like Plex: `kia_jellyfin_up`, `kia_jellyfin_sessions`, and `kia_jellyfin_session_info`, with `KIA_JELLYFIN_URL` and `KIA_JELLYFIN_TOKEN` (an API key from Jellyfin) set. Any Jellyfin works, in the stack or not. Its players get pseudonyms from the same list as Plex's.
- kia makes the TV-only playlist (`tv.m3u`) always, not only when your list has radio, so the address Jellyfin and other players are set up with keeps working.
- The [Jellyfin guide](docs/jellyfin.md) is checked against Jellyfin 12.3, says how often Jellyfin reads the guide again, and explains channel names that lose a leading number.

### Radio

- SomaFM and KEXP, from what the stations publish themselves. A `somafm:` line in `sources.txt` adds SomaFM's listener-supported, ad-free channels (51 now), with their genres and logos, from SomaFM's own channel list, so new channels arrive and retired ones go by themselves; `somafm: channels=groovesalad,dronezone` picks some. A `kexp:` line adds KEXP, Seattle's listener-supported station, and kia reads KEXP's show schedule from its public API, so its shows and DJs appear in the radio guide, like KINK's. Both lines are in `sources.txt`, turned off, and on the Dashboard's playlist list.

### Observability

- New Hardware dashboard: the graphics card (load, its video encoder and decoder, temperature, power, memory), each disk's health (SMART: failing its own check, replaced, pending, and unreadable sectors, cable errors, temperature, hours on, NVMe wear), temperatures and fans, software RAID (failed disks, rebuilding, in sync), memory errors, the processor slowing for heat, free space, and a UPS or laptop battery. Each section fills in from its own source.
- Two new opt-in profiles, for Linux: `monitoring-gpu` runs NVIDIA's exporter for the graphics card (it needs NVIDIA's Container Toolkit; no extra privileges), and `monitoring-disks` runs the disks' health exporter (privileged, as reading SMART data takes; no published port). `KIA_DCGM_IMAGE` and `KIA_SMARTCTL_IMAGE` choose their versions.
- `kia_plex_sessions` has a new label, `hw`: whether Plex converts video on the graphics card (`gpu`) or the processor (`cpu`). The Plex dashboard graphs it, and the Hardware dashboard counts both.
- The [observability cookbook](docs/cookbooks/observability.md) now has a Linux path and a Mac path. On a Mac, node-exporter comes from Homebrew (`brew install node_exporter`), since one in Docker would measure Docker's own small Linux machine rather than the Mac.
- The TV stack dashboard's **Memory used** was empty on a Mac, whose node-exporter reports memory differently. It now shows what Activity Monitor calls Memory Used.
- Grafana's dashboards went empty about a minute after Grafana started, with Grafana 13.2 or newer. Grafana updated its bundled Prometheus plugin in the background, which meant deleting it from Grafana's read-only system; that failed after Grafana had already dropped the plugin. kia now turns off Grafana's background plugin installs and updates (Grafana's own settings); kia's dashboards need only the bundled Prometheus plugin, and updating kia brings a new Grafana with its plugins.

### Plex

- `kia plex-tuners` on a new Plex in the stack has Plex download its codecs first, which Live TV needs, by playing a few seconds of video. It always made a library for a clip of its own, and that library waited in Plex's queue while Plex scanned another one; after 90 seconds kia gave up with "Plex didn't find the warm-up clip". Now kia plays one of your own videos with AAC sound when Plex has one, and makes its clip only when there's none. While Plex scans another library first, kia says which and waits for it, up to 20 minutes.
- On a Mac with Apple silicon (and on other Arm computers), Plex names its AAC codec `libaac_lc_decoder.so`, so kia never saw the codecs arrive and said Live TV might fail. It looks for both names now.
- A new Plex in the stack turns away changes for its first minutes ("the server is still starting up"), although `docker compose ps` already says it's healthy, so `kia plex-tuners` right after starting Plex failed with "Plex answered 400". kia now waits for it, up to 5 minutes.
- New `scripts/nfs-media.sh` mounts shares from a NAS or other servers (NFS) for Plex in the stack, read-only, on Linux and on a Mac, and keeps them after restarts: [media on a NAS or another server](docs/cookbooks/plex-box.md#media-on-a-nas-or-another-server). On Linux, each share mounts when it's first read, after the network is up, so a server that's off or slow to answer at startup doesn't leave Plex with an empty folder until the next restart.

### Mac

- Plex's database broke on a Mac. Docker there runs in a Linux machine of its own, and reaches the kia folder through a file share that Plex's database doesn't survive: scanning a library logged hundreds of database errors and left the database damaged. On a Mac, the installer now keeps Plex's data (`KIA_PLEX_CONFIG=plex-config`) and Grafana's (`KIA_GRAFANA_DATA=grafana-data`) in Docker volumes, inside Docker's own machine. Linux is unchanged.
- kia reads the token that Plex in the stack saved when it was claimed. It found it only in `plex/config` in the kia folder, so a `KIA_PLEX_CONFIG` somewhere else, or a volume, left kia without it. Now kia gets Plex's settings, read-only, from wherever `KIA_PLEX_CONFIG` puts them.
- Uninstalling is now `docker compose down --rmi all --volumes`, so the volumes go too.

### Known problem

- On a new Docker install on Linux, which keeps images in containerd, cAdvisor can't see the containers, so the TV stack dashboard's container graphs are empty. The computer's own graphs work. To be fixed in 0.5.5.

## 0.5.3 (2026-10-23)

### Upgrading from 0.5.2

Update with `scripts/update.sh`, when nobody is watching: kia restarts. Do it before the next nightly refresh if you can (see below).

### Sturdier

- iptv-org/epg stopped bringing esbuild on 2026-10-23 (it now runs its scripts with `@swc-node/register` instead of `tsx`), so kia couldn't compile them for Node's sandbox. A new install ran the guide downloader without the sandbox, and an existing one would have kept using scripts compiled from the older code, against the new code's dependencies. Now kia installs its own esbuild (a fixed version, from npm, without running install scripts) into `vendor/kia-tools` when the checkout has none, and never keeps compiled scripts from an older checkout: if it can't compile them, it runs iptv-org's code its own way and says so in the log. CI's weekly check against the newest iptv-org/epg and the smoke test both caught the change.

### Dashboard

- The **Graphs** button is now called **Observability**. It and **Documentation** open in a new tab, so the Dashboard stays where it was.

## 0.5.2 (2026-10-22)

### Upgrading from 0.5.1

0.5.1 doesn't have `scripts/update.sh` yet, so update this once by hand, in the kia folder, when nobody is watching: `git fetch --tags`, `git checkout v0.5.2`, then `docker compose up -d --build`. kia and the web server restart. Then restart the Threadfins once, so they start asking for the login: `docker compose restart threadfin threadfin-2` (just `threadfin` with one). From here on, `scripts/update.sh` does the updating.

### Security

- kia believed any container on Docker's network that said a request came through the secure page. Threadfin, Grafana, or Plex could reach kia directly and say so too (the password was still needed). Now kia believes only its own web server.
- kia's certificate key could be read by every user of the computer, so that nginx could read it. Now only you and your group can, and the web server's container joins your group. An existing key is fixed when kia starts. If you use your own certificate, the [secure page guide](docs/secure-page.md#using-your-own-certificate) now uses `chmod 640` for its key.
- Plex in the stack keeps only the capabilities it needs to start and to stop cleanly, and can't gain more. cAdvisor gets a read-only system, no capabilities, and only Docker's socket instead of all of `/var/run`, with the same numbers as before.
- New setting `KIA_NODE_EXPORTER_LISTEN`, to keep node-exporter off the rest of your network.
- Threadfin's page had no login, so anyone on your network could change its playlist or FFmpeg settings. kia now gives each Threadfin without a login one: the user `admin`, with a random password in `threadfin/conf/admin-password.txt` (and `threadfin-2/conf/`). Plex's access to the tuner, playlist, and guide is unchanged. For a Threadfin that's already running, the login starts with its next restart (see *Upgrading*); one that already has users of its own is left alone.

### Privacy

- `/metrics` showed each Plex player by its name, which is often a room's or a person's, to anyone on your network. Now each player has a pseudonym, such as `player-3fa2c1`: the same for a device every time, and impossible to work back from without a secret kia keeps in `state/`. The System page on the secure Dashboard shows which is which, after the password (or `GET /api/players`). Graphs recorded before keep the names until Prometheus lets them go (30 days by default); to drop them now, delete `monitoring/prometheus/` while Prometheus is stopped.

### Sturdier

- Only one refresh runs at a time. A refresh started by hand (`docker compose exec kia python3 -m kia run`) could run alongside the nightly one, or the one a new install starts by itself, and the two could overwrite each other's files. Now the second says a refresh is already running, and since when, and stops; the nightly run skips its turn if one started by hand is still going. Saving on the Dashboard also waits for a refresh's writes in another process, not only in its own.

### Updating

- `scripts/install.sh` now installs kia's newest release, instead of whatever was merged since (`--main` still follows that).
- New `scripts/update.sh` updates kia in one step: it moves to the newest release (or `v0.5.2`, or `--main`), rebuilds or pulls the images, and restarts. If someone is watching through Pluto TV or Plex's Live TV, it asks first. It stops if one of kia's own files was changed, instead of losing the change. Without Docker, `scripts/update.sh --no-docker`. The README and the other docs now use it.

## 0.5.1 (2026-10-22)

### Upgrading from 0.5.0

Update as usual (the README's steps), when nobody is watching: kia restarts, and Grafana with monitoring on. The first refresh afterwards compiles iptv-org's scripts for Node's sandbox, which takes a few seconds. Without Docker, the sandbox needs Node.js 20 or newer; with an older one, kia runs the guide downloader as before and says so in its log. A Grafana that's already set up keeps its password.

### Security

- iptv-org's guide downloader is third-party code that changes every day. kia now runs it in Node.js's sandbox: it can read only its own folder and the channel list, write only the guide it makes and a temporary folder, and start no other programs. It also no longer sees kia's settings, so a bad change upstream couldn't read your Plex token, email password, or Dashboard password. kia compiles the downloader's scripts first, with the esbuild that comes with it, because compiling them inside the sandbox would mean starting another program. Without Docker this needs Node.js 20 or newer.
- Channel source addresses can hold a provider's user name and password. **Check now** showed the address it was checking in full to anyone on your network, on the plain (http) port too. Now the logins are hidden there, and on the secure page they show only after the password.
- Grafana no longer starts with the password `admin` when `KIA_GRAFANA_PASSWORD` is empty: it makes a random one on its first start and saves it in `monitoring/grafana/admin-password.txt`. A Grafana that's already set up keeps its password.
- `scripts/pin-versions.sh` and `scripts/install-service.sh` keep `.env` (and its backups) readable only by you.
- A channel source with an impossible port number no longer breaks the Sources page.

### Sturdier

- A control character in a channel's name (from someone else's playlist) made the whole guide unreadable for every channel, night after night, and the playlists' own guides failed to build. kia now drops those characters when it reads a playlist, and again when it writes the guide.
- When a channel source couldn't be downloaded (a timeout at 4 a.m.), all of its channels left your playlists until the next night. kia now tries twice more, after 10 and 30 seconds, and if it still can't, uses the copy it downloaded last time (up to a week old). Its streams are still checked that night.
- The Pluto TV relay kept a piece of video that failed to download, so every player stalled at it. Now it tries that piece again.
- **Stop** on a **Check now** that had only just started said nothing was running. It also could answer with an error if the check ended at the same moment.
- The Pluto relay follows redirects only to https addresses, and **Test** only tries stream addresses, as the nightly check does.
- kia's password file stays private even if kia stopped while writing it.

### Dashboard

- The Timeline no longer freezes the page. With a big playlist it rebuilt every row once a minute (up to two seconds on a fast computer), losing your place and the keyboard focus each time. Now it fills in shows only for the rows on screen and as you scroll, moves the "now" line each minute, and rebuilds only when the half hour changes, without losing your place. Its hour labels stay in view as you scroll down, and on a phone a tap anywhere in a row's height opens the show at that time.
- Things that should be hidden stayed visible because of their styles: the "On now / Timeline" buttons and "Hide channels without guide data" before there's a guide, an empty Pluto TV table on phones, and the playlist editor's channel filter, which didn't filter.
- **Copy** works on the plain (http) address too, where browsers don't offer the clipboard; if even that fails, the address is selected for you to copy. Screen readers hear the result.
- **Check now** keeps going when kia doesn't answer for a moment (it says so and tries again), instead of showing "0 streams". Its clock is no longer read aloud every two seconds.
- Buttons that start something (**Test**, **Check now**, **Remove**, **Put back**, **Renumber everything**, **Delete playlist**) ignore more clicks until it's done.
- After **Cancel**, adding a channel source, or removing one, the keyboard focus stays where you were instead of going back to the top of the page. Messages are read out more reliably by screen readers.
- Leaving a channel's number box saves the new number; on a phone, the keyboard's Done key used to throw it away. Escape still cancels.
- Searching for a channel to add shows only the latest search's results.
- The **Getting started** ticks are easier to tap (44 pixels). The Pluto TV table and the Sources page explain themselves in plainer words, and "Remove it until it works" is now **Take it out until it works**.

### Install and docs

- On a Raspberry Pi, or any computer with less than 6 GB of memory, `scripts/install.sh` now writes the lighter settings into `.env` itself, so the first refresh doesn't run out of memory.
- **Uninstalling** warns that the kia folder can hold your movies and Plex library (the home TV box) and the graphs' history, before you delete it.
- **Troubleshooting** no longer asks you to post your `.env`, which holds your passwords: `kia settings` shows the same without them.
- One set of update steps everywhere (the README's), so the base images get updated too.
- "Add these lines to `.env`" is now "set these lines": the lines are already in `.env`, and an added line can be quietly overridden by the empty one further down.
- Corrections: the default alert events, which folders kia can write to and what the optional services can reach (SECURITY.md), what `scripts/pin-versions.sh` pins, the Docker form of `kia backup` and `kia plex-tuners`, how often Plex's playlists refresh, the time zone and email port defaults, the Dashboard's pages, and smaller fixes.

### Checks

- A smoke test (`scripts/smoke-test.sh`) starts kia's whole stack with a test stream, runs a full refresh, and checks the playlist, the guide, `/metrics`, the Dashboard's pages, and both ports. CI runs it on every pull request.
- Every week CI checks kia against the newest iptv-org/epg, and fails if its scripts can no longer run in Node's sandbox.
- Property tests feed odd text through the playlist reader and the guide writer; they catch the control-character bug fixed above.
- Images are scanned before they're published, and a fixable high or critical CVE stops the release. Each image carries an SBOM and a signed build attestation, and the image that was pushed is scanned again by its digest. Before, the scan after a release could run before the new image existed, and scan the one before it.
- CI checks `compose.yaml` with every set of profiles, reports Grype's findings next to Trivy's, and lists the code the tests never run.

## 0.5.0 (2026-10-22)

### Upgrading from 0.4

- kia's image is now built on [Wolfi](https://github.com/wolfi-dev) instead of Alpine. Run `git pull` and `docker compose up -d --build`. If you pinned `KIA_NODE_IMAGE` in `.env`, it's no longer used: run `scripts/pin-versions.sh` again to pin the new base (`KIA_BASE_IMAGE`), or delete the line.
- That `docker compose up -d` recreates every container once, Threadfin and Plex too, to apply the new log limits. Pick a time when nobody's watching.

### A safer image

- kia's image is built on Wolfi, whose packages get security fixes quickly. It matters most for ffmpeg, which reads streams from anywhere on the internet: Wolfi ships ffmpeg 9.0 (Alpine 8.1). Grype finds 7 known CVEs in the new image against 46 in the old one; Trivy finds none in either. The image is about 80 MB bigger. A full refresh gave the same results in the same time.
- New settings: `KIA_BASE_IMAGE` (the base, by default `cgr.dev/chainguard/wolfi-base:latest`) and `KIA_NODE_VERSION` (the Node.js major version, default 24). `scripts/pin-versions.sh` pins the base by digest.
- CI's image scan no longer skips any known CVEs. The three in npm that 0.4.1 let through (`.trivyignore`) are fixed in the npm the image now has (12.2.1).

### A home TV box with Plex

- New cookbook, [A home TV box](docs/cookbooks/plex-box.md): kia, Threadfin, and Plex Media Server together in Docker on one computer, set up from one `.env`. It's the first of the [cookbooks](docs/cookbooks/README.md), which each take one goal from start to finish.
- Plex can run in kia's stack (`COMPOSE_PROFILES=plex,plex-server`), claimed to your account on first start (`PLEX_CLAIM`). Its settings and your media folder are kept on disk (`KIA_PLEX_CONFIG`, `KIA_PLEX_TRANSCODE`, `KIA_MEDIA_DIR`, which Plex can read but not change), and it gets a proper name (`KIA_PLEX_NAME`, default `kia-tv-box`).
- kia sets Threadfin up the first time it starts: your playlist and its guide, a filter for every channel, kia's guide, 4 tuners, and FFmpeg options that pass on one stream. It never changes Threadfin's settings after that. `KIA_THREADFIN_PLAYLIST` and `KIA_THREADFIN_2_PLAYLIST` choose the playlists.
- `kia plex-tuners` works with Plex in the stack without a token (it uses Plex's own), reaches the Threadfins by name on Docker's network, and gets a brand-new Plex its codecs first. Without them Plex can't play any Live TV channel ("Could not tune channel"), and Live TV alone never makes it download them.
- With `KIA_PLEX_URL` set, kia keeps Plex up to date after each refresh, so new channels arrive in Plex by themselves.

### Dashboard

- The guide timeline works on phones: 3 hours that fit the screen instead of 6 that scroll sideways, with the channel names kept in view. Tap or click a show to see its full title and times. The button is now called **Timeline**.
- **Stop** on a running **Check now**, for a big playlist that's taking too long. It ends the check's stream tests too, and kia's container now runs a small init (`init: true`) that cleans up after them.
- Easier to read and to tap: buttons and text boxes have darker edges, the green text and the coverage bar's amber have more contrast, and on touch screens every button is at least 44 pixels tall. A test keeps the colours at WCAG AA contrast in both themes.

### Metrics

- kia publishes its numbers for Prometheus at `/metrics` ([metrics](docs/metrics.md)): the last refresh, channels, guide coverage, streams per source, playlists, the Pluto relay's viewers and problems, Plex's sessions (with `KIA_PLEX_URL` set), and kia's processor and memory use.
- New cookbook, [Graphs for your TV stack](docs/cookbooks/observability.md): Prometheus, Grafana, and cAdvisor in kia's stack (`COMPOSE_PROFILES=...,monitoring`, plus `monitoring-node` for node-exporter), with three dashboards: kia, TV stack, and Plex. Grafana is on port 8788 and Prometheus on 8789, away from the busy 3000 and 9090.
- With monitoring on, the Dashboard has a **Graphs** button that opens Grafana (or set `KIA_GRAFANA_URL`).

### Computers that are off at night

- When kia starts and finds it missed the last nightly run, because the computer was off at that time, it refreshes straight away instead of waiting a day. A laptop or desktop no longer needs `KIA_RUN_ON_START`, which refreshes at every start.
- Without Docker on a Mac, `scripts/install-service.sh` waits and tries again when launchd won't restart kia straight away. Before, it could stop with kia not running.
- A refresh cut short (the computer shut down, or kia stopped part way) runs again when kia starts. Before, the Dashboard kept saying it was refreshing, and **Check now** refused, until the next night.
- New cookbook, [Kodi and kia on one computer](docs/cookbooks/kodi-computer.md): a laptop or desktop that finds the channels and plays them, with nothing open to the network. The installer no longer opens firewall ports for a kia that only this computer uses (`localhost`).
- New cookbook, [A laptop that travels](docs/cookbooks/travel-laptop.md): the laptop checks the streams where you are and copies the guide from your home server, with what happens away from home.
- New cookbook, [A TV in every room](docs/cookbooks/every-room.md): one server for the house, a player on each device, and a playlist for each room or person.

### Logs

- Docker keeps at most 30 MB of each container's log: 3 files of 10 MB, dropping the oldest lines first (`KIA_LOG_MAX_SIZE`, `KIA_LOG_MAX_FILES`). Before, it kept every line until the container was recreated, which can fill a disk over months. The next `docker compose up -d` recreates every container once to apply it.

### Docs

- The diagrams are easier to read on GitHub, in light and dark mode: no more labels on grey or text hidden behind GitHub's zoom buttons. They're now images, made from sources in `docs/diagrams/` by `scripts/diagrams.sh`, so they look the same everywhere.

## 0.4.3 (2026-10-21)

### Upgrading from 0.4.2

Update as usual: `git pull`, then `docker compose up -d --build`. If the Dashboard then warns that others can read `.env`, run `chmod 600 .env` in the kia folder.

### Faster on small computers

- Saving a playlist on the Dashboard used to rebuild every playlist's guide before the page answered: 2 to 7 seconds on the PCs we measured, about 10 on a Raspberry Pi 4, and 30 on a Pi 3B. Now kia rewrites only the guides whose channels changed, and the full set only when the guide itself is new.
- The TV-only playlist's guide is the full guide under a second name instead of a copy. Radio has almost no listings, and players ignore channels they don't have. That saves about 40% of the work of every guide refresh, and 3 MB.

### Fixes

- A channel you remove because it won't play now comes back on its own only after its stream works three nights in a row, instead of after one. kia's check only opens a stream, and some streams that open still don't play well, so they kept coming back every night. **Put back** still works straight away.

### Security

- The installer makes `.env` readable only by you, and tightens an existing one. It holds kia's password, and your email, alert, and Plex settings if you use them. The Dashboard warns if anyone else on the computer can read it, and the manual install steps include `chmod 600 .env`.

## 0.4.2 (2026-10-21)

### Upgrading from 0.4.1

Update as usual: `git pull`, then `docker compose up -d --build`.

### Plex

- New: `kia plex-tuners` adds kia's Threadfins to Plex for you. It puts them in one DVR of their own, each with its own guide named after its kia playlist, turns on every channel, and leaves any other DVR alone. Plex's own settings page can't do this when Plex already has a DVR, such as an antenna tuner. Running it again picks up new channels, and `--remove` deletes the Threadfin DVR. It needs Plex's address and a Plex token in `.env` (`KIA_PLEX_URL`, `KIA_PLEX_TOKEN`). See [Plex](docs/plex.md#let-kia-add-them).

### Docs

- The Plex guide said **Add Another Device** adds Threadfin to a DVR you already have. That works only when the DVR already uses an XMLTV guide; with Plex's own guide, Plex offers only antenna and cable lineups. The guide now says to use `kia plex-tuners` in that case.

## 0.4.1 (2026-10-21)

### Upgrading from 0.4.0

Update as usual (`git pull`, then `docker compose up -d --build`), then run `docker compose up -d --force-recreate web`. The web server has a new security setting, and it runs with its old settings until it's recreated. Bookmarks to the old Details page still work: it points to where each part went.

### Dashboard

- The status page is now the **Dashboard**, with a menu at the top of every page: **Dashboard**, **Playlists**, **Sources**, **Guide**, and **System**. The long Details page is split between them, and `details.html` points to where things went.
- A **Documentation** button at the top right opens the docs, and every page ends with a link to the troubleshooting guide, in place of the Help card.
- The Dashboard keeps what you check most: whether kia is working, the channel counts, alerts, and the playlist addresses. The counts open the full playlist and the Guide page.
- Long playlist pages show your favorites and the first 200 channels, with **Show all**; the filter still searches every channel. **Hide channels without guide data** leaves out channels the guide has nothing for.
- The Getting started checklist is shorter: finished steps fold to one line, and the heading counts how many are done.
- Each group of settings on the System page folds away.
- The two things called "Add a playlist" have their own names now: **Make a playlist** for the playlists you make for people and devices, and **Channel sources** (with **Add a channel source**) for the lists kia reads channels from.
- Saving, errors, and test results show next to what you changed, not at the top of a long page; the Channel sources list no longer redraws and closes the sections you opened. Errors look like errors everywhere.
- Changing a channel number says which channels moved and where; clicking away from the number box cancels instead of saving. The TV-only playlist's page no longer offers number changes.
- On phones, each channel's Test button sits with its name. Removing a channel asks first, says where to put it back, and test failures are in plain words ("the stream's address doesn't exist any more").
- The playlist editor shows pinned channels by name, can filter long category lists, asks before saving a playlist with no channels, says when it's saving, and confirms afterwards.
- The password help matches where your password comes from, always says what to do if you forget it, and the System page and checklist update after you change it.
- Empty states: a filter with no matches, a playlist with no channels, no guide yet, and pages opened as files each say so. A hidden Getting started can be brought back from a link on the Dashboard; a `localhost` address is no longer ticked as reachable by other devices.
- Buttons and boxes have labels for screen readers, and keyboard focus stays where you were.

### Plex

- Threadfin, which shows kia's playlists to Plex as a TV tuner, can now run with kia: add `COMPOSE_PROFILES=plex` to `.env`. It starts and stops with kia, keeps its settings in the kia folder, and is part of kia's backups. `COMPOSE_PROFILES=plex,plex-2` runs a second one, because Plex takes at most 480 channels from each tuner. Their ports and image are set in `.env` (`KIA_THREADFIN_PORT`, `KIA_THREADFIN_2_PORT`, `KIA_THREADFIN_IMAGE`).
- A new profile setting, **For Plex through Threadfin** (`threadfin = true`), writes channel names with a curly apostrophe. Threadfin turns a plain one into a double quote, which broke those channels' lines and their numbers.
- A new profile setting, `exclude_channels`, leaves out channels by ID, whatever else the profile says. It keeps two Plex playlists from sharing channels.
- Saving a playlist on the Dashboard no longer drops its `exclude_channels`.

### Security

- A crafted name pattern in the playlist editor's preview could keep kia busy for hours, stopping playback and the Dashboard for everyone. Previews that use name patterns now need the password, like saving.
- A crafted backup could have written files outside kia's own settings when restored. Restore now writes only kia's settings files and folders, never as programs, and backs up linked certificates as real files.
- Text from radio-browser.info, which anyone can edit, could add an unchecked channel to your playlists. Playlist lines are now always one line each.
- kia's own server answers bad or unexpected requests with an error instead of hanging or dropping the connection, lets go of clients that go quiet, and checks one password at a time.
- The Pluto TV relay plays only channels in your playlists, at most 20 at once, with a bounded cache, and fetches only https addresses.
- Playlist addresses with a provider's login in them are shown in full only on the secure page. kia's pages can't be shown inside another website.
- kia's own certificate key is readable only by you when kia runs without Docker (with Docker, the web server still needs to read it). Passwords kia makes, on first start or with `kia password --reset`, are no longer printed; they're in `state/admin-password.txt`. The macOS service file, which can hold your settings, is readable only by you.
- kia's own web server no longer serves a half-written file when its name is disguised with `%` codes.

### Fixes

- The Pluto TV relay writes the problems it meets (Pluto going back, errors, falling back to the plain link) to kia's log as well as the Dashboard, so they can still be looked at after the channel stops. It also answers Kodi's request for the `/pluto/` folder instead of returning an error.
- Pluto TV channels that reach kia through the Samsung TV Plus list now play through kia's Pluto relay too, so they keep playing across ad breaks.
- A channel number chosen on the Dashboard can be moved to another channel or cleared, and the change shows right away instead of after the next night.
- With `channel_numbers = false`, changes on the Dashboard no longer add numbers to the playlists.
- The nightly run and changes made on the Dashboard at the same moment can't undo each other, and "Check now" no longer rewrites the stream history.
- When a nightly run can't start (a mistake in `kia.toml`, say), kia now marks it failed on the Dashboard and sends the `run_failed` alert, instead of only logging it.
- The last playlist kia uses can't be removed from the Dashboard, and `concurrency` or `per_host` of 0 in `kia.toml` is refused instead of making the check wait forever.
- New installs start with `KIA_PUBLIC_URL` empty instead of an example address that looked real, so the guide and Pluto TV links can't point at a computer that doesn't exist. The Getting started checklist doesn't count the old example as set.
- "Check now" could miss a finished check's result; it now tells a new report from the old one reliably.
- Building kia sends only its code to Docker, never `.env`, `state/`, or `tls/`.
- A test release tag (such as `v0.4.1-rc1`) no longer replaces the published `latest` image.

### Docs

- Diagrams show the nightly steps, how Pluto TV channels play through kia, the setups, the Dashboard's two ports, and profiles. The Dashboard screenshots are new.
- The Plex guide is rewritten and tested end to end: Threadfin started with kia, one or two tuners, the Threadfin settings that work (including FFmpeg options that pass on one stream instead of every quality at once), adding the tuners in Plex, diagrams and screenshots, and fixes for what goes wrong, such as a Plex Web bug in Firefox.
- The 48 kHz audio note covers Kodi's Fixed output setting, and a TV left at the wrong rate after Kodi quits.
- The quick install now waits for the first refresh before setting up Kodi, explains how to open a terminal, and includes the Raspberry Pi settings. The installer prints full links to the guides.
- New: what to do when kia won't start, how to change kia's address, and how to uninstall. kia doesn't run on Windows yet, and the docs now say so.
- Corrections to the security policy, the profiles example, the files kia keeps (`state/` holds the password), the API overview, and the update steps, which now point to each version's upgrade notes.

### The kia image

- The image now runs as an ordinary user even without compose.yaml, and picks up Alpine's and npm's security fixes when it's built.
- Every pull request scans the image for known vulnerabilities (Trivy) and checks kia's code with Ruff and Bandit, its scripts with ShellCheck, and its workflows with zizmor. The published image is scanned again every week, and the results show in the repository's Security tab.

### Under the hood

- Building and reading the guide keeps memory flat on large guides.
- The test suite runs in about 7 seconds instead of 22, and its timing-dependent tests no longer fail on a busy machine.

## 0.4.0 (2026-10-20)

### Upgrading from 0.3.0

Update as usual (`git pull`, then `docker compose up -d --build`), then run `docker compose up -d --force-recreate web`. The web server has a new setting for Pluto TV channels, and it runs with its old settings until it's recreated.

### Pluto TV keeps playing through ad breaks

- Pluto TV channels used to freeze, go black, or jump back minutes at ad breaks in most players, and came back only when started again. They now play through kia, which opens its own Pluto session with Pluto putting in the ads, leaves out pieces Pluto repeats or sends again, and puts every piece on one steady clock. It works the same for every player, and timeshift keeps working. See [Pluto TV](docs/how-it-works.md#pluto-tv).
- The playlist points Pluto channels at kia (`/pluto/<id>.m3u8`). `KIA_PLUTO_RELAY=false` in `.env` (or `relay = false` under `[pluto]` in `kia.toml`) goes back to Pluto's own links.
- The Details page shows the Pluto channels playing right now, with their sessions and recent problems, and `pluto.json` has the same for scripts. The home page warns you if Pluto channels are playing without kia's fix, and the new `pluto_fallback` alert (off by default) can tell you too.

### Docker healthchecks

- Both containers have healthchecks: kia's own server and the web server must answer every minute. `docker compose ps` shows `(healthy)` or `(unhealthy)`, and tools that watch Docker can use it.

### Testing a channel that won't play

- Each channel on a playlist's page (secure page) has a **Test** button that tries its stream now. If it doesn't play, **Remove it until it works** takes it out of every playlist straight away; the nightly check puts it back when the stream works again, and the Everything page lists removed channels with **Put back**. See [a channel won't play](docs/troubleshooting.md#a-channel-wont-play).

### Backup and restore

- `kia backup` saves your settings, sources file, own playlists, profiles, `state/` (password, numbers, favorites, playlist choices, history), and the certificate in one file in `work/backups/`. `kia restore` puts them back, after saving the current ones so it can be undone. See [backing up](docs/configuration.md#backing-up-and-moving-to-another-computer).

### Published images

- kia's Docker image is published for amd64 and arm64 at `ghcr.io/southsidedean/kodi-iptv-assistant`: each release as its version and `latest`, and `main` for what's merged. Set `KIA_IMAGE` in `.env` to use it instead of building kia yourself, which helps most on a Raspberry Pi. See [the published image](docs/configuration.md#the-published-image).

### Email alerts

- Alerts can go by email, through your email provider's server: set `KIA_SMTP_HOST`, `KIA_SMTP_USER`, `KIA_SMTP_PASSWORD`, and `KIA_EMAIL_TO` in `.env`. `kia test-alert` sends a test. [Alerts](docs/alerts.md#email) has the settings for Gmail, Outlook.com, iCloud, and Fastmail.

### TV-only playlist

- When your list has radio stations, kia also makes a TV-only playlist, `tv.m3u`, with its own guide. Jellyfin and some other players show radio stations as TV channels, so [Jellyfin's guide](docs/jellyfin.md) now uses it instead of asking you to make a profile. A profile of your own named `tv` replaces it.

### Choosing playlists on the status page

- The secure status page's Details page lists every playlist kia uses and every one `sources.txt` offers turned off, grouped like the file, with each one's streams from the last check. Tick a playlist to use it and untick it to stop, or add any M3U playlist by its address. kia keeps these choices in `state/sources.json` and applies them on top of your sources file at every run, so the file stays yours to edit by hand. See [choosing playlists on the status page](docs/configuration.md#choosing-channel-sources-on-the-dashboard).
- **Check now** tests every stream in one playlist straight away and shows how many work, without changing your playlists.
- The same is in the API: `GET`, `PUT`, `POST`, and `DELETE /api/sources`, and `/api/sources/check`.

### Favorites and channel numbers

- Star channels on the secure status page to make them favorites. Favorites come first in every playlist and get a Favorites category, which Kodi and most players show as a group, and which a profile can pick for a playlist of just your favorites.
- Change a channel's number on the Everything page. It works like a `tvg-chno` in your own playlist file: a channel that had the number moves to the next free one, and a number in your own file still wins.
- Playlists list TV channels first, then radio stations, each with favorites at the top. A playlist's page shows TV and radio under their own headings, in the list and in the **Next 6 hours** view.
- **Renumber everything** (on the Everything page, or `kia renumber`) numbers every channel 1, 2, 3 down the list once, keeping the numbers you chose. Clear Kodi's TV data afterwards.
- `recycle_numbers_after_days` under `[output]` in `kia.toml` lets a new channel reuse the number of one that's been gone that long (60 days or more; off by default), so numbers stop climbing. See [gaps in the numbers](docs/configuration.md#gaps-in-the-numbers-and-renumbering).
- Stars, numbers, and renumbering apply straight away, without waiting for a refresh. kia keeps your stars and numbers in `state/channels.json`. The API has `GET /api/channel-choices` and `PUT /api/channel`.

### Fixes

- kia no longer raises a `dns_local` alert when a name fails to look up and the public DNS servers it double-checks with can't be reached either. Nothing then shows that your DNS is at fault (a moment without internet is more likely), so kia notes it in the log instead.

### Status page

- A new kia's home page starts with a **Getting started** checklist: the first refresh, the address other devices use, a player on each device, the password, choosing playlists, and alerts. kia ticks what it can check; you tick the rest (your browser remembers), or hide it.
- The Details page lists the settings kia is running with, and where to change each one (`.env` or `kia.toml`). Passwords and alert addresses show only whether they're set. `kia settings` shows the same in a terminal, and `status.json` has it under `settings`.

## 0.3.0 (2026-10-20)

### Upgrading from 0.2.0

Update as usual (`git pull`, then `docker compose up -d --build`). Both containers are recreated, because they gained folders and a port:

- The status page is now also at `https://<your address>:8743/`, which is where you make changes. Make sure nothing else on the machine uses port 8743, or pick another with `KIA_HTTPS_PORT` in `.env`. The first visit shows a browser warning about kia's own certificate; [the secure status page](docs/secure-page.md#the-browser-warning) explains it.
- kia makes a password for changes and saves it in `state/admin-password.txt`. Set `KIA_ADMIN_PASSWORD` in `.env` to choose your own.
- kia writes two new folders, `profiles/` and `tls/`. `git pull` creates them; if you copied kia some other way, create them yourself so Docker doesn't make them owned by root.
- If the web container keeps running the old settings after an update, run `docker compose up -d --force-recreate web`.

### Playlists for people and devices

- Profiles make smaller playlists from the full one, each with its own address, such as `http://<your address>:8780/kids.m3u` for the kids' tablet. A profile picks channels by category, playlist, name, or ID, and can leave out categories, names, TV, or radio. See [profiles](docs/profiles.md).
- Make and change profiles on the status page. The form shows how many channels a playlist would have as you change it. Profiles live in `profiles/profiles.toml`, which you can also edit by hand (start from `profiles/example.toml`).
- Each profile gets its own guide with only its channels. In a test, a 90-channel kids profile's guide was 391 KB instead of 3 MB, which is much less for a slow device to load.
- `renumber = true` numbers a profile's channels from 1. Otherwise a channel has the same number in every playlist.

### Status page

- The status page is also on a secure port, `https://<your address>:8743/`, where all changes are made; playlists and guides stay on plain HTTP for the players. kia makes a self-signed certificate and renews it. To use your own, from Tailscale or Let's Encrypt for example, put it in `tls/`. `KIA_TLS_NAMES` sets the names it covers, and `kia tls` shows what's installed. See [the secure status page](docs/secure-page.md).
- kia makes an admin password on first start (in `state/admin-password.txt`), which you can change on the status page. A password in `.env` (`KIA_ADMIN_PASSWORD`) still wins. `kia password --reset` makes a new one, and `KIA_EDITING=false` turns editing off.
- The [API guide](docs/api.md) covers reading kia's status and changing profiles from scripts and other apps.
- The home page is shorter: the status, the channel counts, any alerts, and your playlists with their addresses and Copy buttons.
- Each playlist has its own page with its addresses and a filterable list of its channels, showing what's on now and next. **Next 6 hours** shows the same as a timeline.
- Guide coverage, streams by playlist, and the reports moved to a Details page.
- The kia name and logo at the top are bigger.

### Faster, steadier nightly runs

- The stream check tests up to six streams at a time on jmp2.uk, which carries the Pluto TV, Samsung TV Plus, and Plex channels, instead of two (`per_host_limits` in `kia.toml`). In a test of 60 of its streams, that took 29 seconds instead of 98, with no extra failures.
- Tests on the same server start at least 100 ms apart (`host_delay_ms`), so bursts don't set off a site's firewall.
- Guide sites can have their own number of connections and a pause between requests (`site_connections`, `site_delay_ms`). tvpassport.com now uses 100 connections with a 100 ms pause: its download took 44 seconds instead of 16½ minutes, with complete data. plex.tv gets one connection at a time, which it needs.
- A guide site that takes longer than 30 minutes is stopped, and its channels keep their previous guide (`site_time_limit`).
- ontvtonight.com is skipped by default; it sends programs without titles.

### Installing

- `scripts/install.sh` installs kia with Docker on macOS or Linux in one step: it downloads kia, finds the address and time zone, makes `.env`, opens the firewall ports if it can, starts kia, and prints the status page's address and the password. Running it again keeps your settings.
- `scripts/install-service.sh` runs kia without Docker as a service that starts with the computer: a systemd user service on Linux, a launchd agent on macOS (templates in `deploy/`). kia runs straight from its folder; there's nothing to install from Python.
- `kia daemon` takes its daily time from `KIA_RUN_AT` too.

### Docs

- [Start here](docs/start-here.md) explains what kia gives you, the three steps to get there, and the words you'll see. A [docs index](docs/README.md) lists every guide by task.
- The install guide and README make choosing playlists optional (the USA list is enough to start), and the troubleshooting guide covers the status page: the browser warning, the password, and refused changes.
- Fuller credits for the iptv-org community and the other playlist and guide sources, and a link to the Kodi Wiki.

### Guides for other players

- New setup guides for [Plex](docs/plex.md) (through Threadfin), [Jellyfin and Emby](docs/jellyfin.md), [TiviMate](docs/tivimate.md), [VLC](docs/vlc.md), and [phones and tablets](docs/phones.md), with a page to help [choose a player](docs/players.md). The Jellyfin and Threadfin steps were tested against kia.

### Sources

- `sources.txt` has new options, each tested from a US home connection: Xumo and Local Now (channels iptv-org doesn't list, without guide data) and genre radio lists from github.com/junguler (worldwide; about half the stations work, and kia keeps those).
- A playlist line in the sources file can end with `radio`, for radio playlists that don't mark their stations.

### Program guide

- `accept_name_matches` in `kia.toml` turns on "possible matches" from the mapping report that you've checked.
- Reports show times in your time zone, such as "Mon Oct 19, 15:55 EDT".

### Versions

- `scripts/pin-versions.sh` writes the exact versions you're running into `.env`, and `--unpin` goes back to the latest.

## 0.2.0 (2026-10-19)

### Upgrading from 0.1.1

In Kodi, on each device, change IPTV Simple Client's **Auto refresh mode** to *Repeated refresh* and set **Refresh interval** to 120. kia now refreshes the guide every few hours, and Kodi picks that up only if it reloads more than once a day ([Kodi setup](docs/kodi.md#3-point-it-at-the-playlist)).

### Program guide

- Between nightly runs, kia refreshes the guide every 4 hours (`KIA_GUIDE_REFRESH_HOURS`, `0` turns it off). Only the guides kia reads itself are downloaded again (i.mjh.nz, kink.nl, and the playlists' own guides), which takes seconds. Some of those list only the next few hours: i.mjh.nz's Samsung TV Plus guide reaches about 11 hours ahead. For a playlist that names many guides (Free-TV names about 100), a quick refresh downloads only the ones that had your channels the night before. `kia refresh-guide` does the same by hand. The status page shows when the guide was last updated.
- When a channel's listings end before the guide does, the rest of its row shows the channel's name instead of nothing.
- Channels that no guide site lists by ID are now matched by name: exactly the same name, in the same country, belonging to one channel only. On the test server this gave 86 more TV channels full listings (from 42% to 48%). The matches are listed in `mapping-report.html`, and `ignore_name_matches` in `kia.toml` turns off a wrong one. Looser matches appear there as suggestions only.
- kia now uses the guide a playlist names in its first line (`x-tvg-url`) for that playlist's channels. It downloads the guide, keeps the channels in your list, and compares it with iptv-org's sites. `sources.txt` adds such a list, Samsung TV Plus (USA) from apsattv.com, off by default. `playlist_guides = false` in `kia.toml` turns this off.
- The same channel listed by different playlists under different IDs now appears once. kia joins channels with exactly the same name from different playlists, keeps the ID, logo, and number from the earliest playlist in your sources list, and plays whichever copy's stream tested best. Its guide can come from any of the copies. `merge_duplicates = false` under `[check]` turns this off.
- Channel IDs that differ only in capital letters now match (`SYFY.us@East` and `Syfy.us@East`).
- Programs without a title no longer count as guide data. ontvtonight.com currently sends only times, which showed as empty blocks in Kodi; those channels now use another site or show their name.

### Radio

- For a radio station named exactly "KINK", kia reads KINK's program schedule from kink.nl, so Kodi's radio guide lists its shows and DJs.

### Status page and reports

- The status page is easier to read at a glance: a colored banner with the state of the last refresh, big numbers for channels and guide data, bars for guide coverage and for each playlist's working streams, and a Copy button for the playlist address.
- The status page and reports share one look that works on phones as well as computers. Light and dark follow the device's setting, and each system uses its own fonts, so nothing loads from the internet.
- kia has a logo: a screen showing program guide rows, with the current time marked in amber. It's the browser tab icon, and it sits at the top of the status page and every report, where clicking it returns to the status page.

### Fixes

- Accented channel names now show correctly in playlists saved as Windows-1252 instead of UTF-8, such as apsattv.com's. "AMC en Español" appeared as "AMC en Espa�ol".
- Channels that passed kia's check but wouldn't play in Kodi now play. Kodi introduced itself as Kodi, and some servers refuse that: jmp2.uk, which carries the Pluto TV, Samsung TV Plus, and Plex channels in the iptv-org lists, answered with "403 Forbidden". The playlist now tells Kodi to introduce itself the way kia did when it tested each stream (`#EXTVLCOPT:http-user-agent`).

## 0.1.1 (2026-10-19)

Fixes from an independent review of 0.1.0, locked-down containers, and versions that follow the latest releases by default. New: steady channel numbers, a status page, and reports you can read in a browser.

### Upgrading from 0.1.0

1. In the kia folder, run:

   ```sh
   git pull
   docker compose build --pull
   docker compose pull --ignore-buildable
   docker compose up -d
   ```

   If `git pull` stops because you edited `sources.txt` or `kia.toml`, move your playlist choices to your own copy first ([configuration](docs/configuration.md)), then run `git checkout sources.txt kia.toml` and pull again.
2. If you changed the folder settings in `kia.toml` (`dir`, `state_dir`, `repo_dir`, `work_dir`), set them back. kia can now write only to its standard folders.
3. In Kodi, on each device, turn on IPTV Simple Client's daily refresh and Kodi's *Use channel numbers from backend* ([Kodi setup](docs/kodi.md), steps 3 and 5).

The first run after upgrading numbers every channel, in alphabetical order, so channel numbers change once. After that they stay put. `http://<your address>:8780/` shows the new status page; links straight to `curated.m3u` and `guide.xml.gz` work as before.

### Kodi

- Channels keep the same number from night to night (`tvg-chno`), instead of being renumbered whenever a stream drops out or a playlist is added. A channel that comes back within a month gets its old number. Turn on Kodi's *Use channel numbers from backend* to see them ([Kodi setup](docs/kodi.md)). TV and radio are numbered separately. To choose numbers yourself, give channels a `tvg-chno` in a playlist file of your own ([how](docs/configuration.md#favorites-and-your-own-channel-numbers)); numbers from internet playlists are ignored.
- The Kodi setup now includes IPTV Simple Client's daily refresh (*Auto refresh mode: Once per day*). Without it, Kodi loads the list and guide only when it starts, so devices that sleep instead of shutting down kept old data.

### Radio

- Your own playlist files can live in a `playlists/` folder, which git ignores. A station marked `radio="true"` there shows under Radio, so you can add stations radio-browser.info lists badly, such as KINK's theme channels.
- `KIA_RADIO_PLACEHOLDERS=true` in `.env` fills Kodi's radio guide with each station's name, as TV channels without guide data already do.

### Status page

- `http://<your address>:8780/` now shows a status page instead of a file list: when kia last refreshed, whether it worked, the next refresh, TV, radio, and guide counts, the last run's alerts, links to the reports, and links to the documentation for setup and troubleshooting. `status.json` has the same information for scripts and monitoring tools.
- Each report now also comes as an HTML page that reads well in a browser, with a filter box on long tables (the stream report lists every channel). The Markdown versions are still written.

### Versions

- kia now uses the latest Node.js LTS (`node:lts-alpine`), the latest nginx (`nginx:alpine`), and the latest iptv-org/epg (`KIA_EPG_GIT_REF=latest`, its default branch) unless you pin a version in `.env`. Updating kia now pulls the newest images too. Tested with Node.js 24.22.0 and nginx 1.31.7.

### Security

- Both containers run read-only, with no extra privileges. kia sees the project folder read-only and writes only to `output/`, `state/`, `work/`, and `vendor/`, so the third-party guide downloader can't change kia or its settings. The web server sees only `output/` and doesn't follow symbolic links.
- Only network streams (http, https, rtmp, rtsp) are handed to ffprobe. Guide site names are checked before they're used as file names. Only the `#KODIPROP` lines that choose a stream's player are copied from playlists. Git hooks are turned off in kia's copy of iptv-org/epg.

### Fixes

- A dropped connection while copying the guide from an upstream server no longer crashes the run or leaves a partial guide.
- A guide site with only old listings no longer beats the placeholder that shows the channel's name.
- The guide is still built when no channel matches a guide site, when GitHub can't be reached, when installing the downloader fails, or when one site's guide file is damaged.
- A mistyped `KIA_NTFY_URL` is logged instead of crashing the alert, and the run.
- A single failed name lookup during a guide download no longer raises a DNS alert; names are tried again first.
- A site whose own DNS servers send broken IPv6 answers is no longer reported as a problem with your network's DNS. When this computer's DNS answers the site's IPv4 lookup, kia knows your DNS works.
- On the night clocks change for daylight saving time, the refresh runs at `KIA_RUN_AT` instead of an hour early or late.
- DNS double-checks handle international names (such as `bücher.de`), and names that can't exist are reported as invalid instead of blaming the public DNS servers.
- Playlists and sources files that start with a byte-order mark (some Windows editors add one) are read correctly.
- Reports are replaced in one step, so a browser never shows a half-written one.
- The second try for slow streams keeps the limit of two tests per server.
- Test runs (`kia check` with `--limit`, `-s`, or `-u`) write to `work/check-test/` and leave the live playlist alone.

### Docs

- New: a troubleshooting page, organized by what you see, linked from the README and the status page. Also new: when changes take effect and how to refresh right away, versions and how to pin them, and checking the guide address when the guide stays empty.
- Fixed: the setup recipes no longer put comments on the same line as settings, which Docker read as part of the value. The quick start uses your own copy of `sources.txt`. Also corrected: the `grace_failures` wording, `PUID`/`PGID`, installing `curl` before Docker on Linux, IPTV Simple Client from Ubuntu and Debian packages, and running without Docker.

## 0.1.0 (2026-10-16)

The first release. kia turns free IPTV playlists into a working channel list with a matching program guide for Kodi, refreshed every night and served on your network.

### Channel list

- Tests every stream in the playlists you choose with ffprobe and keeps one working stream per channel, preferring higher resolution and faster response.
- Keeps a stream that worked before until it fails several nightly checks in a row, since free streams often drop out for a while.
- Introduces itself to servers as VLC, the player most widely accepted, and tests at most two streams per server at a time. Streams that fail a name lookup or time out get a second, slower try.
- `sources.txt` ships with a checked catalog of public playlists (iptv-org countries, languages, categories, states, and providers, plus Free-TV), with the USA list on. `KIA_SOURCES_FILE` lets each install keep its own list that git won't touch.
- Internet radio from radio-browser.info: lines such as `radio-browser: language=english top=100` add the most popular working stations, which Kodi shows under Radio.

### Program guide

- Matches channels to guide sources from iptv-org/epg using the playlist's own channel IDs, downloads guide data from several sites, and keeps the best source for each channel.
- Reads i.mjh.nz's guide files (Pluto TV, Plex, Samsung TV Plus, Roku) directly, in seconds instead of about 25 minutes.
- Channels with no guide data show their name in the guide instead of a blank row.
- The playlist carries the guide's address, so Kodi needs only one setting: the playlist URL.

### Running it

- Docker Compose setup with a small web server, configured through `.env`. Tested on Linux and on macOS (Apple silicon).
- Runs daily at a set time and catches up after a computer wakes from sleep.
- Upstream mode for laptops: test streams locally and copy the guide from a home kia server when it can be reached.
- `kia serve` for installs without Docker.

### Alerts

- Tells a local DNS failure apart from a dead stream by checking public DNS.
- Optional notifications through ntfy or any command, for a failed nightly run, local DNS trouble, sharp drops in channels or guide coverage, and guide sites that keep failing. `kia test-alert` checks the setup.

### Known limitations

- About half of the channels in the US list have no guide data from any source yet.
- Running kia itself needs macOS or Linux. Kodi on any platform, including Windows, can use it.
- Radio stations rarely have guide data.

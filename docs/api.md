# Using kia from scripts and other apps

Everything the Dashboard shows, and everything it can change, is also available to scripts, home automation, and other apps. The status, playlists, and guides are files you can read on the plain HTTP port. The API under `/api/` reads and changes [profiles](profiles.md), the [playlists kia checks](configuration.md#choosing-channel-sources-on-the-dashboard), favorites and channel numbers, the password, and [the stack](stack.md), adds Live TV to Plex, tests or removes channels, and gives `kia doctor`'s answers. Reading works on either port, and changes work only on the secure port. kia's [MCP server](mcp.md), for AI assistants, is under `/api/` too, on the secure port only.

The examples use `192.168.1.20` for the kia machine; use yours.

## Files

| Address | What it is |
|---|---|
| `http://192.168.1.20:8780/status.json` | Whether kia is working, the channel counts, alerts, playlists, the certificate, and the settings. Its fields are listed in [how it works](how-it-works.md#statusjson). |
| `http://192.168.1.20:8780/pluto.json` | The Pluto TV channels playing through kia right now, with their sessions and recent problems. Its fields are listed in [how it works](how-it-works.md#plutojson). |
| `http://192.168.1.20:8780/stack.json` | With Docker, the programs running next to kia and whether each one answered kia's last check. Its fields are listed in [how it works](how-it-works.md#stackjson). |
| `http://192.168.1.20:8780/profiles.json` | Each profile's `name`, `title`, playlist `file`, its own `guide` file, `tv` and `radio` counts, `builtin` (true for kia's TV-only playlist), and `guide_ids` (a fingerprint of the channels in its guide, which kia uses to skip rewriting a guide that hasn't changed), plus `error` when the profiles file has a mistake |
| `http://192.168.1.20:8780/curated.m3u` | The full playlist |
| `http://192.168.1.20:8780/<name>.m3u` | A profile's playlist, or `tv.m3u`, kia's TV-only playlist |
| `http://192.168.1.20:8780/guide.xml.gz` | The full guide (XMLTV, gzipped) |
| `http://192.168.1.20:8780/<name>.guide.xml.gz` | A profile's guide, with only its channels |
| `http://192.168.1.20:8780/guide-preview.json` | The next 14 hours of listings, for "On now": `generated` (when), and `channels`, each channel's ID with a list of `[start, stop, title]` (start and stop in Unix time). Channels without listings are left out. |
| `http://192.168.1.20:8780/report.json` | Every stream from the last check, with its result |
| `http://192.168.1.20:8780/pluto/<id>.m3u8` | A Pluto TV channel, played through kia ([Pluto TV](how-it-works.md#pluto-tv)). This is for players; the playlist has these addresses. |

A file changes only when kia finishes writing it, so a script never reads half of one.

## The API

Requests and answers are JSON. An error comes back with an HTTP status code and a message people can read:

```json
{"error": "Wrong password."}
```

| Request | What it does | Needs |
|---|---|---|
| `GET /api/profiles` | Every profile's settings, plus the categories and source playlists to choose from, with channel counts | |
| `POST /api/preview` | How many channels a profile with the settings you send would have, and a few of their names | |
| `GET /api/channels?q=cnn` | Up to 25 channels whose name or ID contains the text: `id`, `name`, `group` | |
| `PUT /api/profiles/<name>` | Makes the profile, or replaces its settings | password, HTTPS |
| `DELETE /api/profiles/<name>` | Deletes the profile, its playlist, its guide, and its page | password, HTTPS |
| `POST /api/password` | Changes the password: send `{"new": "..."}` | password, HTTPS |
| `GET /api/channel-choices` | Your favorites (channel keys), the numbers chosen on the Dashboard by kind, and the channels removed because they didn't play: `{"favorites": [...], "numbers": {"tv": {...}, "radio": {...}}, "removed": {key: {"name", "when", "entry"}}}` | |
| `PUT /api/channel` | Stars a channel or sets its number: send `{"key": "CNN.us@SD", "favorite": true}` or `{"key": "...", "number": 5}` (`null` clears the number). The key is the channel's ID, or `name:` or `radio:` with its simplified name for channels without one. The playlists are rewritten before the answer comes back. | password, HTTPS |
| `POST /api/channel/test` | Tries a channel's stream now: send `{"key": "..."}`. Returns `ok`, `error`, `seconds`, and `height`. Pluto TV channels are tested at Pluto. | password, HTTPS |
| `POST /api/channel/remove` | Takes a channel out of every playlist until its stream works three nights in a row: send `{"key": "..."}` | password, HTTPS |
| `POST /api/channel/restore` | Puts a removed channel back now: send `{"key": "..."}` | password, HTTPS |
| `POST /api/renumber` | Numbers every channel 1, 2, 3 down the list once, keeping chosen numbers ([renumbering](configuration.md#gaps-in-the-numbers-and-renumbering)). Returns the `tv` and `radio` counts. | password, HTTPS |
| `GET /api/sources` | The playlists kia can check, in `playlists`: each one's `url`, `name`, `label`, `section`, whether it's `radio`, whether it's `on`, where it comes from (`origin`: `file` or `added`), whether it was `changed` on the Dashboard, and its `streams` from the last check. Also `file` (the sources file in use), `editable`, `secure`, and the last "check now" (`check`). A user name, password, or query in an address shows only with the password in `X-Kia-Password`, over HTTPS; otherwise it's hidden (`•••`). `unlocked` says which you got, and `locked` says why a password that came with the request didn't unlock them (wrong, or too many wrong ones). | |
| `PUT /api/sources` | Turns a playlist on or off: send `{"url": "...", "on": true}`. The last playlist in use can't be turned off. | password, HTTPS |
| `POST /api/sources` | Adds a playlist: send `{"url": "https://...", "name": "extras", "radio": false}` (`name` and `radio` are optional) | password, HTTPS |
| `DELETE /api/sources?url=...` | Removes a playlist added through the API or the Dashboard | password, HTTPS |
| `POST /api/sources/check` | Tests every stream in one playlist now: send `{"url": "..."}`. One check runs at a time, and none during a refresh. | password, HTTPS |
| `GET /api/sources/check` | The check's progress: `running`, then `result` (`total`, `ok`, `flaky`, `dead`, and a `sample` of working channel names) or `error` | |
| `DELETE /api/sources/check` | Stops the check that's running. Its `error` then says it was stopped. | password, HTTPS |
| `GET /api/stack` | The Stack page: `editable` (whether changes are on) and `secure` (whether this came over HTTPS), `docker` (whether kia runs with Docker), `chosen` (`KIA_STACK`'s parts), `profiles`, the `parts` to choose from and the `presets`, each with its `name`, `title`, `what` (a line about it), `needs` (the memory it needs), `asks` (what it will ask you for), its `parts`, and its `order` on the page, `service` (whether the kia-stack service is running), its `containers` (`service`, `state`, `health`, `status`), `waiting` (the id of a request it hasn't answered yet), and `result`, its last answer (`id`, `action`, `state`, `exit`, `finished`, and `output`, what `scripts/stack.sh` said), and `links`, the ports for Plex and Jellyfin and Grafana's address | |
| `POST /api/stack/check` | Asks the [kia-stack service](stack.md#changing-the-stack-from-the-dashboard) to check a choice: send `{"parts": ["plex", "observability"]}`. Returns its `id`; the answer appears in `GET /api/stack` a few seconds later. One request at a time. | password, HTTPS |
| `POST /api/stack/apply` | The same, but applies the choice: sets `KIA_STACK`, then starts and stops what changed. It doesn't ask first, so check for viewers yourself. For Plex's first start, add `"claim": "claim-..."` from plex.tv/claim. | password, HTTPS |
| `GET /api/plex/tuners` | Adding Live TV to Plex (below): `running`, then `ok` and `output`, what `kia plex-tuners` said | |
| `GET /api/doctor` | `kia doctor`'s overview, in `reports`: each with its `subject`, its `worst` level, and its `findings`, each with a `level` (`problem`, `note`, or `ok`), `title`, `detail`, `evidence` (the files that show it), and `fix`. A login in any address it quotes is hidden | |
| `GET /api/doctor/channel?q=CNN` | The same for the channels matching an ID or part of a name (up to 5) | |
| `GET /api/doctor/plex` | The same for Plex: Threadfin, its playlist, the last time kia added its tuners to Plex, and Plex's codecs | |
| `POST /api/mcp` | kia's [MCP server](mcp.md#for-developers), for AI assistants: JSON-RPC (MCP's Streamable HTTP transport, one JSON answer per request), read-only tools | its token (`Authorization: Bearer`), HTTPS |
| `POST /api/plex/tuners` | Adds kia's channels to Plex as Live TV now (`kia plex-tuners`), which kia otherwise does after each guide refresh. Needs `KIA_PLEX_URL`. One at a time. | password, HTTPS |
| `GET /api/players` | Which pseudonym in `/metrics` and the graphs (such as `player-3fa2c1`) is which Plex or Jellyfin player: each one's `pseudonym`, `name`, `product`, and the day it was last `seen`. | password, HTTPS |

A profile's settings are the ones in [what a profile can say](profiles.md#what-a-profile-can-say), as JSON:

```json
{"title": "The kids", "groups": ["Kids", "Animation"], "radio": false, "renumber": true}
```

A change rewrites `profiles/profiles.toml`, makes the playlist and its guide, and updates the Dashboard, all before the answer comes back. It returns the same contents as `profiles.json`. Changes to the playlists kia checks are saved in `state/sources.json` and return the same contents as `GET /api/sources`; they take effect at the next refresh.

### The password

Send the password in the `X-Kia-Password` header. [The secure Dashboard](secure-page.md#the-password) explains where it is and how to change it. No password gets `401` ("Enter kia's password first."), and a wrong one `401` after a one-second wait. From the second wrong password in a row from one address, kia makes that address wait, doubling each time up to a minute, and answers `429` until the wait is over. Changes sent to the plain HTTP port get `403`, even with the right password.

### Examples

kia's own certificate isn't one your computer trusts, so tell curl to trust that certificate with `--cacert tls/fullchain.pem`. That works on the kia machine, or with a copy of the file. (`-k` skips the check entirely; avoid it outside a quick test.)

Read the profiles:

```sh
curl -s http://192.168.1.20:8780/api/profiles
```

Make a news playlist for the kitchen:

```sh
curl -s --cacert tls/fullchain.pem -X PUT \
  -H "X-Kia-Password: $KIA_PASSWORD" -H "Content-Type: application/json" \
  -d '{"title": "Kitchen", "names": ["news", "weather"], "radio": false}' \
  https://192.168.1.20:8743/api/profiles/kitchen
```

Delete it:

```sh
curl -s --cacert tls/fullchain.pem -X DELETE -H "X-Kia-Password: $KIA_PASSWORD" \
  https://192.168.1.20:8743/api/profiles/kitchen
```

The certificate covers the names in `KIA_TLS_NAMES` (or `KIA_PUBLIC_URL`), so use one of those in the address.

### From a web page

The API answers only pages kia serves itself. A browser asks a site for permission before sending it an `X-Kia-Password` header from another site's page, and kia never gives that permission. kia also refuses any request whose `Origin` is a different address. To use the API from a page of your own, put the page in kia's `output` folder, or call the API from a script or home automation tool instead.

## Commands

The same jobs, and a few more, are `kia` commands. With Docker, run them in the kia folder as `docker compose exec kia python3 -m kia <command>`; without Docker, as `PYTHONPATH=src python3 -m kia <command>`. `--help` after any of them lists its options.

| Command | What it does |
|---|---|
| `run` | A full refresh, once: `check`, then `map`, `grab`, and `guide` (or `sync` with an upstream server) |
| `check` | Tests every stream and writes the playlists. `--source <name>` checks one playlist, `--limit <n>` the first few streams, for a test that doesn't change what players use |
| `map` | Matches the channels to guide sites ([how the guide gets built](guide-under-the-hood.md)) |
| `grab` | Downloads the guide data. `--site <name>` downloads one site |
| `guide` | Builds the guide from what was downloaded |
| `refresh-guide` | The quick refresh between nights: the quick guide sources only, then the guide |
| `sync` | Takes the guide from another kia server (`KIA_UPSTREAM_URL`) |
| `probe <address>` | Tests one stream address and prints what ffprobe found: the same test as the nightly check, for a stream that isn't in a playlist yet |
| `doctor` | Explains what's wrong, using the files kia keeps, and says what to do. `doctor channel <name>` explains one channel's stream and guide; `doctor plex`, why Plex shows no channels or too few. `--json` gives the same for scripts |
| `settings` | The settings kia is running with, and where each is changed |
| `password` | Where the Dashboard's password is. `--reset` makes a new one |
| `tls` | The secure Dashboard's certificate. `--self-signed` makes a new one of kia's own |
| `renumber` | Numbers every channel 1, 2, 3 down the list, once, keeping chosen numbers |
| `backup`, `restore <file>` | [Backing up and moving](configuration.md#backing-up-and-moving-to-another-computer) |
| `test-alert` | Sends a test [alert](alerts.md) through every way that's set up |
| `mcp-token` | Turns on kia's [MCP server](mcp.md) and shows its token and the setup for Claude Code. `--for <assistant>` shows another's (`claude-desktop`, `gemini`, `codex`, `vscode`, `cursor`, `curl`); `--reset` replaces the token, `--off` turns it off |
| `mcp` | kia's MCP server over standard input and output, for an AI assistant on the same computer, which starts it itself ([on the same computer](mcp.md#on-the-same-computer-as-kia)) |
| `plex-tuners` | Adds kia's Threadfins to Plex as Live TV, or brings them up to date ([Plex](plex.md#let-kia-add-them)). `--remove` takes them out again |
| `check-dashboards` | Which Grafana panels have data on this computer; `scripts/stack.sh` shows the same ([choosing the parts](stack.md)) |
| `daemon` | What kia's container runs: a refresh every night at `KIA_RUN_AT`, the quick refreshes in between, and with `--serve`, the web server |
| `serve` | Only the web server, without refreshes (for installs without Docker) |
| `threadfin-setup` | Writes each Threadfin's first settings. Its own container runs it ([under the hood](plex-under-the-hood.md#threadfins-first-start)) |

## Stability

The files and the API follow kia's version numbers: a field or request can be added in any release, and anything removed or changed is listed in the [changelog](../CHANGELOG.md) under that release. Scripts should ignore fields they don't know.

# kia and AI assistants (MCP)

kia has an MCP server: tools an AI assistant can call to look at kia's record, and to ask kia to do a few things that you then approve on the Dashboard. With it you can ask your assistant, in your own words, "is kia working?", "why does ESPN have no guide?", or "which playlists are failing?", and it reads kia's answers to reply. MCP (the Model Context Protocol) is the standard most assistants use for this.

The answers are the same ones **Ask kia what's wrong**, **Why?**, and `kia doctor` give ([asking kia for help](ask-kia.md)). The assistant can combine them and explain them, and you don't have to know where to look.

On this page:

- [What you can ask](#what-you-can-ask)
- [Which assistants can use it](#which-assistants-can-use-it)
- [On the same computer as kia](#on-the-same-computer-as-kia)
- [From another computer](#from-another-computer): turn it on, trust kia's certificate, then [your assistant](#set-up-your-assistant)
- [Several devices: the gateway](#several-devices-the-gateway): a key for each device, with a limit and a log
- [More than one kia](#more-than-one-kia): every kia in one assistant
- [If a device is lost or stolen](#if-a-device-is-lost-or-stolen): what to change, and where
- [What the assistant can see](#what-the-assistant-can-see)
- [Asking kia to act](#asking-kia-to-act): requests you approve on the Dashboard
- [Letting requests run by themselves](#letting-requests-run-by-themselves): for an hour, or always
- [Privacy and safety](#privacy-and-safety)
- [When it doesn't work](#when-it-doesnt-work)
- [For developers](#for-developers)

## What you can ask

- "Is kia working? Anything I should fix?"
- "Why does Hogan's Heroes show only its name in the guide?"
- "Which playlists have the most dead streams, and what should I do?"
- "Is everything in my stack running?"
- "Find the news channels kia has, and tell me which have a full guide."
- "What settings is kia running with, and where do I change the refresh time?"
- "Why does Plex show no channels?"
- "Are my disks OK?"
- "What's on CNN right now, and after that?"
- "When is the football game on, and on which channel?"
- "What sports are on right now?"
- "How do I make a playlist for the kids?" (from kia's own docs)

Two of these come ready-made, as starter prompts: **kia checkup** asks for a check of kia and an explanation of anything that needs you, and **explain a channel** asks about one channel's stream, guide, and what's on. In Claude Code, type `/` to find them (`/kia:kia-checkup`), or run `/mcp__kia__kia-checkup` and `/mcp__kia__kia-channel CNN`. Claude Code splits what follows at spaces, so give one word of the channel's name, or its ID. Other assistants list them with their MCP server's prompts.

The assistant decides which of kia's tools to use. kia's findings say which file they come from, so it can tell you where to look too.

## Which assistants can use it

An assistant can use kia's MCP server if it runs on a computer in your home and lets you add a server by its address. Assistants that run in a company's cloud can't reach kia, because their servers would have to connect to your home network from the internet.

| Assistant | Works with kia | How we checked |
|---|---|---|
| [Claude Code](#claude-code) | Yes | Tested, end to end |
| [Gemini CLI](#gemini-cli) | Yes | Tested, end to end |
| [Claude Desktop](#claude-desktop) | Yes, through a small bridge program | The bridge tested; Claude Desktop's side from Anthropic's docs |
| [Codex (OpenAI's CLI)](#codex) | Yes | Its setup tested; the connection from OpenAI's docs and Codex's source |
| [VS Code (Copilot)](#vs-code) | Yes | From Microsoft's docs |
| [Cursor](#cursor) | Yes | From Cursor's docs |
| [Any other MCP client](#any-other-mcp-client) | If it supports MCP over HTTP with a header | |
| claude.ai, the Claude apps' custom connectors | No: they connect from Anthropic's cloud | [Anthropic's help](https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp) |
| ChatGPT | No: it connects only from OpenAI's cloud | [OpenAI's help](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) |

Don't open a port on your router to let a cloud assistant in: kia is meant for your home network. ChatGPT Business and Enterprise also offer a [secure MCP tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) that connects out from your network instead; we haven't tried it with kia.

## On the same computer as kia

The simplest setup: if the assistant runs on the computer that runs kia, it can start kia's MCP server itself, as a program, with `kia mcp`. There's no certificate, token, or network involved, and nothing to turn on first.

With Claude Code, in a terminal (use your kia folder's path):

```sh
# With Docker
claude mcp add --scope user kia -- docker compose --project-directory ~/kodi-iptv-assistant exec -T kia python3 -m kia mcp

# Without Docker
claude mcp add --scope user kia -- env PYTHONPATH=$HOME/kodi-iptv-assistant/src python3 -m kia mcp -c $HOME/kodi-iptv-assistant/kia.toml
```

Other assistants take the same command where their settings ask for a "command" or "stdio" server. Anyone who can run that command can already read kia's files, so it needs no token.

## From another computer

When the assistant runs on another computer, such as your laptop, it reaches kia's MCP server over your network, at `/api/mcp` on the [secure Dashboard](secure-page.md)'s port. It answers only over HTTPS, and only to whoever has its token.

### 1. Turn it on

In the kia folder, on the computer running kia:

```sh
docker compose exec kia python3 -m kia mcp-token
```

(Without Docker: `PYTHONPATH=src python3 -m kia mcp-token`.) It prints the server's address, its token, and the setup for Claude Code. For another assistant, add `--for` and its name: `claude-desktop`, `gemini`, `codex`, `vscode`, `cursor`, or `curl`. The setup it prints has your address and token filled in, ready to paste.

Keep the token private, like a password. Always use the `https://` address it prints: a token sent to kia's plain `http://` port is refused, but it has crossed your network unprotected by then.

The address is the first name kia's certificate covers: `KIA_TLS_NAMES` if you set it, otherwise the host in `KIA_PUBLIC_URL`. Any other name or address for the same computer fails, even once the certificate is trusted. If it prints `localhost`, set `KIA_PUBLIC_URL` in `.env`, restart kia, and run it again. Without Docker, other computers reach kia only when `.env` has `KIA_BIND=0.0.0.0` (the setting it starts with).

### 2. Trust kia's certificate

kia's own certificate is one it made itself, which no assistant trusts until you tell it to. There are two ways:

- Use a certificate every computer already trusts ([your own certificate](secure-page.md#using-your-own-certificate), for example [with Tailscale](secure-page.md#with-tailscale) or [Let's Encrypt](secure-page.md#with-lets-encrypt)). Then there's nothing to do on each computer, and the steps below skip the certificate.
- Or copy kia's certificate to each computer: copy `tls/fullchain.pem` from the kia folder, and save it as `kia-cert.pem` in your home folder. The setup for each assistant below says how to point it there.

kia makes a new certificate when its address changes, and before the old one expires (every 825 days). Copy the new `tls/fullchain.pem` across when it does.

### Set up your assistant

Each of these needs the address and token from step 1. `kia mcp-token --for <name>` prints the same with yours filled in.

#### Claude Code

```sh
claude mcp add --scope user --transport http kia https://192.168.1.20:8743/api/mcp --header "Authorization: Bearer <token>"
NODE_EXTRA_CA_CERTS=~/kia-cert.pem claude
```

To trust the certificate every time, add `export NODE_EXTRA_CA_CERTS=~/kia-cert.pem` to your shell's startup file (`~/.zshrc` on a Mac). Claude Code also trusts the certificates your computer trusts, so adding kia's there (Keychain Access on a Mac) works too. In Claude Code, `/mcp` shows kia as connected, with twenty-one tools.

Claude Code saves the token in its settings (`~/.claude.json`), and your shell keeps the command in its history: keep both private.

#### Gemini CLI

```sh
gemini mcp add --scope user --transport http --header "Authorization: Bearer <token>" kia https://192.168.1.20:8743/api/mcp
NODE_EXTRA_CA_CERTS=~/kia-cert.pem gemini
```

Gemini CLI turns on MCP servers only in folders you trust: say yes when it asks, the first time you start it in a folder. `gemini mcp list` shows kia as connected.

#### Claude Desktop

Claude Desktop starts local programs, not servers at an address, so it reaches kia through a small bridge, [mcp-remote](https://github.com/geelen/mcp-remote), which needs [Node.js](https://nodejs.org/). In Claude Desktop, open **Settings**, then **Developer**, then **Edit Config**, and add kia under `"mcpServers"`:

```json
{
  "mcpServers": {
    "kia": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://192.168.1.20:8743/api/mcp", "--transport", "http-only", "--header", "Authorization:${KIA_AUTH}"],
      "env": {
        "KIA_AUTH": "Bearer <token>",
        "NODE_EXTRA_CA_CERTS": "/path/to/kia-cert.pem"
      }
    }
  }
}
```

Use the full path to your copy of the certificate. `Authorization:${KIA_AUTH}` has no space after the colon on purpose: on Windows, Claude Desktop breaks arguments at spaces. Restart Claude Desktop. Its custom connectors, under **Settings**, are a different thing: they connect from Anthropic's cloud and can't reach kia.

#### Codex

Add kia to `~/.codex/config.toml`:

```toml
[mcp_servers.kia]
url = "https://192.168.1.20:8743/api/mcp"
bearer_token_env_var = "KIA_MCP_TOKEN"
```

Then start Codex with the token, and trusting kia's certificate:

```sh
KIA_MCP_TOKEN=<token> CODEX_CA_CERTIFICATE=~/kia-cert.pem codex
```

The token stays out of the config file. `codex mcp list` shows kia.

#### VS Code

In VS Code, run **MCP: Open User Configuration** from the Command Palette, and add kia. VS Code asks for the token the first time and keeps it out of the file:

```json
{
  "inputs": [
    { "type": "promptString", "id": "kia-token", "description": "kia's MCP token", "password": true }
  ],
  "servers": {
    "kia": {
      "type": "http",
      "url": "https://192.168.1.20:8743/api/mcp",
      "headers": { "Authorization": "Bearer ${input:kia-token}" }
    }
  }
}
```

VS Code doesn't document how it trusts a certificate like kia's own. If it can't connect, use [a certificate every computer trusts](#2-trust-kias-certificate).

#### Cursor

Add kia to `~/.cursor/mcp.json`, under `"mcpServers"`:

```json
{
  "mcpServers": {
    "kia": {
      "url": "https://192.168.1.20:8743/api/mcp",
      "headers": { "Authorization": "Bearer ${env:KIA_MCP_TOKEN}" }
    }
  }
}
```

Set `KIA_MCP_TOKEN` in your shell's startup file, and restart Cursor. Like VS Code, Cursor doesn't document trusting a certificate like kia's own; if it can't connect, use [a certificate every computer trusts](#2-trust-kias-certificate). Cursor's MCP log (**Output**, then **MCP Logs**) shows why a connection failed.

#### Any other MCP client

kia speaks MCP's Streamable HTTP transport. Give your client:

- the address from `kia mcp-token`, ending in `/api/mcp`
- the header `Authorization: Bearer <token>`
- kia's certificate, unless you use one every computer trusts

A client that only starts local programs can use the [mcp-remote](#claude-desktop) bridge, as Claude Desktop does.

## Several devices: the gateway

kia's own token is enough for one or two assistants. When several people or devices use kia's MCP server, the gateway part gives each device a key of its own. You can see which device asked what, limit each one, and turn off one device's key without changing the others. It's [agentgateway](https://agentgateway.dev), an open-source gateway for AI tools, running next to kia. It's optional, and it needs Docker; without it, kia's own protections stay as they are.

### 1. Add the gateway part

In the kia folder, add `gateway` to `KIA_STACK` in `.env`, such as `KIA_STACK=plex,gateway`, then:

```sh
scripts/stack.sh apply
```

Or choose **Gateway** on the Dashboard's Stack page ([choosing the parts](stack.md)). The gateway answers on port 8745 (`KIA_GATEWAY_PORT`), over HTTPS with kia's certificate, at `/mcp`.

### 2. Give each device a key

```sh
scripts/gateway.sh add laptop
```

It prints the device's key, once, and the setup for Claude Code with the gateway's address and that key filled in. For another assistant, add `--for` and its name, as with `kia mcp-token`: `scripts/gateway.sh add office-pc --for gemini`. Name each device so you'll know it in the log, with lowercase letters, digits, and dashes. The device also needs [kia's certificate](#2-trust-kias-certificate), as before.

`scripts/gateway.sh list` shows the devices that have a key. `scripts/gateway.sh remove laptop` turns that device's key off at once. Adding or removing a device restarts the gateway, which takes about a second. A key can't be shown again: if one is lost, remove the device and add it again for a new key.

### 3. Turn kia's own token off (optional)

Once your devices use the gateway, kia's own token is needed only by assistants you set up before. Turn it off, and every call has to come through the gateway:

```sh
docker compose exec kia python3 -m kia mcp-token --off
```

The gateway keeps working: it has a token of its own for kia, in `gateway/kia-token`, which no device ever sees. `kia mcp`, [on kia's own computer](#on-the-same-computer-as-kia), needs no token either way.

### What the gateway adds

`docker compose logs gateway` shows a line for each call, with the time, the device, the tool, the answer's status, and how long it took. It never shows a key or a token.

Each device can make 120 requests a minute (`KIA_GATEWAY_RATE`), and an assistant's start-up takes a few of them. Over the limit, that device's assistant gets "rate limit exceeded" for a moment while the others carry on. kia's own limit of 60 tool calls a minute applies to each device too, and kia's log names the device each call came from.

The gateway keeps each key only as its SHA-256, in `gateway/`, so its settings can't give a key away. Backups keep the list of devices and leave the gateway's token out; after restoring one, `scripts/stack.sh apply` makes a new token.

## More than one kia

An assistant can use several kia installs at once, such as one in the den and one on a Raspberry Pi at the cabin. Each kia has a name: this computer's, unless you change it on the Dashboard's **System** page or with `KIA_NAME` in `.env`. Give each kia a different one.

1. On each kia, run `kia mcp-token --for <assistant>` as above. Its setup names the server after that kia, such as `kia-den` and `kia-cabin-pi`, so adding the second doesn't replace the first. For Codex and Cursor, each kia's token goes in a variable of its own, such as `KIA_MCP_TOKEN_DEN`.
2. Put every kia's certificate in the one file the assistant trusts. Copy each kia's `tls/fullchain.pem` to this computer, then join them:

   ```sh
   cat den.pem cabin-pi.pem > ~/kia-cert.pem
   ```

   Claude Code, Gemini CLI, Claude Desktop, and Codex each read a single file, and trust every certificate in it. Skip this for a kia whose certificate every computer already trusts.
3. For an assistant set up in a file (Claude Desktop, VS Code, Cursor, and Codex), add each kia's server next to the ones already there, rather than pasting over the file. With two kias, Claude Desktop's `claude_desktop_config.json` holds both, each pointing at the joined certificate file:

   ```json
   {
     "mcpServers": {
       "kia-den": {
         "command": "npx",
         "args": ["-y", "mcp-remote", "https://192.168.1.20:8743/api/mcp", "--transport", "http-only",
                  "--header", "Authorization:${KIA_AUTH}"],
         "env": {"KIA_AUTH": "Bearer <den's token>", "NODE_EXTRA_CA_CERTS": "/full/path/to/kia-cert.pem"}
       },
       "kia-cabin-pi": {
         "command": "npx",
         "args": ["-y", "mcp-remote", "https://192.0.2.10:8743/api/mcp", "--transport", "http-only",
                  "--header", "Authorization:${KIA_AUTH}"],
         "env": {"KIA_AUTH": "Bearer <cabin-pi's token>", "NODE_EXTRA_CA_CERTS": "/full/path/to/kia-cert.pem"}
       }
     }
   }
   ```

   VS Code is the same, with each kia's entry under `inputs` too. For Codex and Cursor, set every kia's token variable, such as `KIA_MCP_TOKEN_DEN=... KIA_MCP_TOKEN_CABIN_PI=... codex`.

The assistant then sees each kia's tools under its own server's name, and each kia tells it its name, so you can ask "is the guide working on cabin-pi?". With the gateway part, each kia has its own gateway, and a device needs a key from each.

## If a device is lost or stolen

An assistant's setup on that device holds what it needs to reach kia: a token or a gateway key. Change what it had, on each kia it used. kia's certificate needs nothing: it isn't a secret.

1. **A gateway key** (the device has a name in `scripts/gateway.sh list`): remove it, on each kia whose gateway it used. It stops working at once, and no other device is affected.

   ```sh
   scripts/gateway.sh remove laptop
   ```

2. **kia's own token** (set up with `kia mcp-token`): make a new one, on each kia. The old one stops working at once. Every other device using kia's token then needs the new one, so run `kia mcp-token --for <assistant>` again for each. With the gateway, each device has its own key and this step isn't needed.

   ```sh
   docker compose exec kia python3 -m kia mcp-token --reset
   ```

3. **Requests that run by themselves:** take the device's name out of `auto_devices` in `kia.toml` and restart kia, and press **Stop allowing** on the Dashboard if the card lists anything ([letting requests run by themselves](#letting-requests-run-by-themselves)).
4. **The Dashboard's password**, if the device's browser had it saved: change it on the secure page's **System** page ([changing it](secure-page.md#changing-it)).
5. **Alerts on ntfy**, if the device had the app subscribed: anyone with the topic's name can read your alerts. Make a new topic, and put it in `KIA_NTFY_URL` in `.env` ([alerts](alerts.md#getting-alerts-on-your-phone-ntfy)).

Afterwards, kia's log shows whether anything still tries the old token or key: `docker compose logs kia | grep MCP`, and on the gateway, `docker compose logs gateway`.

## What the assistant can see

| Tool | What it returns |
|---|---|
| `kia_overview` | How the last refresh went, when the next one is, the counts, and anything that needs you |
| `kia_channel` | One channel (or up to 5 that match): whether its stream works and its record of past checks, or why it was left out; where its guide comes from, or why it has none |
| `kia_guide` | The whole guide: when it was rebuilt, how many channels have a full, partial, or no guide, the sites it comes from, sites failing their downloads, and channels that got nothing |
| `kia_pluto` | Pluto TV through kia's fix: what's playing now, any channel playing Pluto's plain link instead, their last problems, and the fallbacks of the last week |
| `kia_setup` | kia's own setup: the secure Dashboard's certificate, the first password, where alerts go, free space, kia's address, and stream tests that all fail |
| `kia_plex` | Why Plex shows no channels, or too few: Threadfin and its playlist, Plex's 480-channel limit, the last time kia added its tuners to Plex, and Plex's codecs |
| `kia_jellyfin` | Why Jellyfin shows no channels, or no guide: the playlist and guide its Live TV reads (and whether they're kia's and go together), its channels, and its last guide refresh |
| `kia_hardware` | The computer, from the stack's Prometheus: disks nearly full or failing their health check, RAID, memory errors, the processor slowing for heat, running on a battery, and the graphics card's temperature |
| `kia_find_channels` | Up to 25 channels whose name or ID contains some text: ID, name, and category |
| `kia_playlists` | The playlists kia checks and how their streams did, and the playlists it makes for people and devices |
| `kia_stack` | Each program running next to kia, and whether it answered kia's last check |
| `kia_settings` | The settings kia is running with, and where each is changed |
| `kia_whats_on` | What's on a channel (or up to 5 that match) now and next, from kia's guide for the next 14 hours. With Plex, also Plex's own channels (an antenna's), by name, call sign, or number |
| `kia_find_shows` | Shows in the guides that match some words or a category, now or up to a week ahead: kia's guide, and Plex's and Jellyfin's own guides (an antenna's, say). Up to 25, the ones named for the words first, each with its channel, number, times, and where to watch it |
| `kia_whats_new` | Channels new to your playlists in the last week, and those gone because their streams stopped working ([new and gone channels](alerts.md#new-and-gone-channels)) |
| `kia_docs` | The sections of kia's own docs, the copy installed with your kia, that best match some words, with links |

That's what the Dashboard shows to anyone on your network, plus a few things from programs in your stack: the names of the computer's disks and where they're mounted (`kia_hardware`, from Prometheus, as Grafana shows them), Jellyfin's Live TV settings (`kia_jellyfin`), and the guide listings of Plex's and Jellyfin's own channels, such as an antenna's (`kia_find_shows`, `kia_whats_on`). These tools only read. Passwords, tokens, and alert addresses show only as set or not set, and user names and passwords inside addresses are hidden.

## Asking kia to act

Some tools ask kia to do something, such as testing a channel's stream or refreshing the guide now. Such a tool doesn't do it: it leaves a request, and nothing happens until you approve it on the Dashboard.

1. You ask your assistant, say, "test CNN's stream". It calls `kia_test_channel`, which leaves a request and gives the assistant a link to the secure Dashboard to pass on to you.
2. The Dashboard's front page shows it under **Requests from assistants**, with what it will do and who asked (the device's name, through the gateway). Approve it with the password, or decline it.
3. kia runs it with the same code as the Dashboard's own button, or the same command kia runs by itself. A stream test answers at once; the others take from a few seconds to a few minutes, and the card shows how it went when it's done. The assistant reads how it went with `kia_request_status`, and tells you.

| Tool | What it asks for |
|---|---|
| `kia_test_channel` | Test one channel's stream now, as the Dashboard's **Test** button does. The channel's ID, or part of its name, must match just one channel |
| `kia_refresh_guide` | Refresh the guide now from the quick sources, as kia does every few hours by itself. With an [upstream kia](guide-under-the-hood.md#with-an-upstream-server), it copies that one's guide again |
| `kia_add_to_plex` | Add kia's channels to Plex as Live TV now, as the **Stack** page's button does. Only once kia knows where Plex is (`KIA_PLEX_URL`) |
| `kia_check_stack` | Check a choice of parts, as the **Stack** page's **Check** does. It only checks: applying a choice stays on the Dashboard. Needs Docker and the kia-stack service |
| `kia_request_status` | How a request went: waiting, running, declined, expired, or done or failed, with its result (this one only reads) |

A request waits 10 minutes, then expires. At most 5 wait at once, one of each kind; asking again for a kind that's waiting gives back the request already waiting. Requests that change your setup, such as applying a stack change, passwords, settings, or sources, can't be asked for at all.

To change how long a request waits, or how many can wait, set them in `kia.toml`:

```toml
[asks]
expires_minutes = 60   # 1 to 1440, a day
waiting = 5            # 1 to 20
```

Restart kia afterwards (`docker compose restart kia`). To hear about each request on your phone, turn on the `assistant_asks` notice ([alerts](alerts.md#choosing-events)); tapping it opens the Dashboard.

## Letting requests run by themselves

Approving every stream test gets old while you work through channels with an assistant. You can let some requests run without asking you. All of this is off until you turn it on, and only three kinds can: stream tests (`test_channel`), guide refreshes (`refresh_guide`), and stack checks (`check_stack`). Adding kia's channels to Plex always waits for you, since it changes Plex's settings.

**For an hour.** On the Dashboard, a waiting request has a third button, **Approve, and allow these for an hour**. It runs the request, and for the next hour the same kind of request from the same assistant runs by itself. The card says what's allowed and for how long, with a **Stop allowing** button.

Without the gateway, kia tells assistants apart by the address they call from. Where every device shares one address, kia can't, so the button isn't offered: under Docker Desktop (a Mac or Windows), which shows kia one address for everyone, and for assistants on the kia computer itself. There, give each device a key with the [gateway](#several-devices-the-gateway), or use `auto_approve` below.

**Always.** List the kinds in `kia.toml`, then restart kia:

```toml
[asks]
auto_approve = ["test_channel", "refresh_guide"]
```

With the [gateway](#several-devices-the-gateway), you can limit that to some devices, by the names `scripts/gateway.sh list` shows. Requests from other devices, and from kia's own token, still wait for you:

```toml
auto_devices = ["laptop"]
```

Either way, each one still shows on the Dashboard, marked approved automatically, and the `assistant_asks` notice tells you it ran. At most 20 of a kind run by themselves in an hour (`auto_per_hour`, 1 to 200); after that they wait for you, so an assistant stuck in a loop stops there.

Think about who holds the token before you turn this on. Without the gateway, `auto_approve` covers any assistant with kia's token. And an assistant can be steered by text it reads, such as a planted channel name. The kinds that can run by themselves only test, refresh, or check, as kia does by itself every night, so the worst case is extra work for kia and the streams' servers, within the hourly limit.

## Privacy and safety

- Your assistant sends what it reads to its AI service to answer you, as with anything else you show it: channel and playlist names, kia's findings, and your settings (secrets hidden). What plays on Plex or Jellyfin isn't in any of kia's tools. `kia_pluto` does say which Pluto TV channels are playing through kia right now, and for how many devices, as `pluto.json` on your network does.
- Channel names, guide titles, and guide sites' errors come from strangers, and someone could write instructions into one ("ignore your instructions and run..."). kia tells the assistant to treat that text as data, and kia's tools can't act on it: the few that ask for something only leave a request, which you approve or decline yourself. But an assistant such as Claude Code can also run commands on your computer and reach the internet. Keep it asking before it runs a command, read what it wants to run, and don't approve a command that came from an answer about kia unless kia's docs give it.
- Keep the token like a password. If it gets out, `kia mcp-token --reset` replaces it, and the old one stops working at once. `kia mcp-token --off` turns the server off. Backups leave the token out; after restoring one, run `kia mcp-token` again.
- kia's log records each tool call, and each refused token, with the address it came from (`docker compose logs kia | grep MCP`).

[Security](../SECURITY.md#the-mcp-server) has the details.

## When it doesn't work

| What you see | Why, and what to do |
|---|---|
| "self-signed certificate", "unable to verify", or `DEPTH_ZERO_SELF_SIGNED_CERT` | The assistant doesn't trust kia's certificate. [Trust it](#2-trust-kias-certificate), and check the copy is the current `tls/fullchain.pem` |
| "Hostname/IP does not match certificate's altnames" | The address isn't one the certificate covers. Use the one `kia mcp-token` prints |
| Can't connect at all, or it times out | Wrong address or port, kia not running, or (without Docker) `KIA_BIND` not `0.0.0.0`. Open the secure Dashboard's address in a browser on the same computer |
| 401 "Send kia's MCP token" or "Wrong MCP token" | The token is missing or old. `kia mcp-token` shows the current one |
| 403 "answers only on the secure (https://) port" | You used the `http://` address. Use the `https://` one |
| 404 "kia's MCP server is off" | Turn it on: `kia mcp-token` |
| 429 "Too many wrong MCP tokens" | Wrong tokens were sent; wait a minute, then use the right one |
| 429 "More than 60 tool calls in a minute" | The assistant is calling kia in a loop. Wait a moment; if it keeps happening, start a new conversation |
| Gemini CLI shows kia as "Disabled" | Gemini CLI turns MCP servers on only in folders you trust |
| 401 "api key authentication failure" (through the gateway) | The device's key is missing, wrong, or removed. `scripts/gateway.sh list` shows the devices; remove the device and add it again for a new key |
| "rate limit exceeded" (through the gateway) | That device made more than `KIA_GATEWAY_RATE` requests in a minute. Wait a moment, or raise the limit and run `scripts/gateway.sh write` |
| 500 "upstream" errors (through the gateway) | The gateway can't reach kia: kia isn't running, or `gateway/kia-token` is missing (run `scripts/gateway.sh write`). `docker compose logs gateway` says which. After a new certificate goes into `tls/`, calls fail for up to a minute, until kia's web server uses it too. With Docker Desktop, also run `scripts/gateway.sh refresh` (the kia-stack service does it within 30 seconds) |
| A channel's guide answer says "not recorded yet" | kia hasn't built the guide since it was updated. It does within a few hours |

## For developers

- **Endpoint:** `POST https://<address>:<secure port>/api/mcp`, MCP's Streamable HTTP transport in its simplest form: one JSON answer per request (no event stream), `202` with no body for a notification or response, `405` for `GET`, no sessions, no JSON-RPC batches. With Docker, nginx terminates HTTPS and passes `/api/` to kia.
- **Versions:** `2025-11-25` and `2025-06-18`. A request with another `MCP-Protocol-Version` gets `400`; `initialize` answers with the client's version if kia has it, else `2025-11-25`.
- **Authentication:** `Authorization: Bearer <token>`, the token from `kia mcp-token` (in `state/mcp-token.txt`). Wrong tokens are slowed down per address, apart from wrong Dashboard passwords; each address can make 60 tool calls a minute. Requests whose `Origin` is another site are refused.
- **Tools:** the twenty-one above. The seventeen that only read have `readOnlyHint: true`; the four that ask have `readOnlyHint: false` and `openWorldHint: true`, and none is `destructiveHint`; two prompts, `kia-checkup` and `kia-channel` (argument `channel`). Results come as JSON text and as `structuredContent`; a tool that fails returns `isError`.
- **Through the gateway:** `POST https://<address>:8745/mcp` with `Authorization: Bearer <device key>`. agentgateway checks the key against its SHA-256 in `gateway/config.yaml`, limits each device, logs the call, and passes it to kia's `/api/mcp` on a network of its own, with its own token (`gateway/kia-token`) and the device's name in `X-Kia-Device`, which kia uses for its limit and log only with that token. It keeps no sessions, as kia doesn't. Over its limit, it answers with JSON-RPC error `-32003`.
- **Locally:** `kia mcp` speaks the same over standard input and output (MCP's stdio transport), one JSON message per line, with kia's own lines on standard error.
- **By hand:**

  ```sh
  curl --cacert ~/kia-cert.pem -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' https://192.168.1.20:8743/api/mcp
  ```

The code is in `src/kia/mcp.py`; the [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25) has the protocol.

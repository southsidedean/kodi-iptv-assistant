# kia and AI assistants (MCP)

kia has an MCP server: read-only tools an AI assistant can call to look at kia's record. With it you can ask your assistant, in your own words, "is kia working?", "why does ESPN have no guide?", or "which playlists are failing?", and it reads kia's answers to reply. MCP (the Model Context Protocol) is the standard most assistants use for this.

The answers are the same ones **Ask kia what's wrong**, **Why?**, and `kia doctor` give ([asking kia for help](ask-kia.md)). The assistant can combine them and explain them, and you don't have to know where to look.

On this page:

- [What you can ask](#what-you-can-ask)
- [Which assistants can use it](#which-assistants-can-use-it)
- [On the same computer as kia](#on-the-same-computer-as-kia)
- [From another computer](#from-another-computer): turn it on, trust kia's certificate, then [your assistant](#set-up-your-assistant)
- [What the assistant can see](#what-the-assistant-can-see)
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

To trust the certificate every time, add `export NODE_EXTRA_CA_CERTS=~/kia-cert.pem` to your shell's startup file (`~/.zshrc` on a Mac). Claude Code also trusts the certificates your computer trusts, so adding kia's there (Keychain Access on a Mac) works too. In Claude Code, `/mcp` shows kia as connected, with six tools.

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
        "NODE_EXTRA_CA_CERTS": "/Users/you/kia-cert.pem"
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

## What the assistant can see

| Tool | What it returns |
|---|---|
| `kia_overview` | How the last refresh went, when the next one is, the counts, and anything that needs you |
| `kia_channel` | One channel (or up to 5 that match): whether its stream works and its record of past checks, or why it was left out; where its guide comes from, or why it has none |
| `kia_find_channels` | Up to 25 channels whose name or ID contains some text: ID, name, and category |
| `kia_playlists` | The playlists kia checks and how their streams did, and the playlists it makes for people and devices |
| `kia_stack` | Each program running next to kia, and whether it answered kia's last check |
| `kia_settings` | The settings kia is running with, and where each is changed |

That's the same as the Dashboard shows to anyone on your network. The tools can't change anything. Passwords, tokens, and alert addresses show only as set or not set, and user names and passwords inside addresses are hidden.

## Privacy and safety

- Your assistant sends what it reads to its AI service to answer you, as with anything else you show it: channel and playlist names, kia's findings, and your settings (secrets hidden). Viewing activity isn't in any of kia's tools.
- Channel names, guide titles, and guide sites' errors come from strangers, and someone could write instructions into one ("ignore your instructions and run..."). kia tells the assistant to treat that text as data, and kia's tools can't act on it. But an assistant such as Claude Code can also run commands on your computer and reach the internet. Keep it asking before it runs a command, read what it wants to run, and don't approve a command that came from an answer about kia unless kia's docs give it.
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
| A channel's guide answer says "not recorded yet" | kia hasn't built the guide since it was updated. It does within a few hours |

## For developers

- **Endpoint:** `POST https://<address>:<secure port>/api/mcp`, MCP's Streamable HTTP transport in its simplest form: one JSON answer per request (no event stream), `202` with no body for a notification or response, `405` for `GET`, no sessions, no JSON-RPC batches. With Docker, nginx terminates HTTPS and passes `/api/` to kia.
- **Versions:** `2025-11-25` and `2025-06-18`. A request with another `MCP-Protocol-Version` gets `400`; `initialize` answers with the client's version if kia has it, else `2025-11-25`.
- **Authentication:** `Authorization: Bearer <token>`, the token from `kia mcp-token` (in `state/mcp-token.txt`). Wrong tokens are slowed down per address, apart from wrong Dashboard passwords; each address can make 60 tool calls a minute. Requests whose `Origin` is another site are refused.
- **Tools:** the six above, each with `readOnlyHint` set. Results come as JSON text and as `structuredContent`; a tool that fails returns `isError`.
- **Locally:** `kia mcp` speaks the same over standard input and output (MCP's stdio transport), one JSON message per line, with kia's own lines on standard error.
- **By hand:**

  ```sh
  curl --cacert ~/kia-cert.pem -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' https://192.168.1.20:8743/api/mcp
  ```

The code is in `src/kia/mcp.py`; the [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25) has the protocol.

# Asking kia for help

kia keeps a record of every refresh: which streams worked, where each channel's guide came from, which guide sites failed, and what runs next to it. You can ask it about that record in two ways, the simplest first:

| | What you get | What it needs |
|---|---|---|
| [1. Ask kia why](#1-ask-kia-why) | Plain answers from the Dashboard or a terminal: what's wrong, where kia saw it, and what to do | Nothing. Works offline, free |
| [2. Ask your own AI assistant](#2-ask-your-own-ai-assistant) | Questions in your own words, answered by an assistant such as Claude Code that reads kia's record through kia's MCP server | An assistant that speaks MCP, and a few minutes to set it up |

Start with the first. The second asks the same questions in plain words, and can combine answers, such as "why is my guide emptier than last week?"

## 1. Ask kia why

- On the Dashboard, **Ask kia what's wrong** shows anything that needs you: a failed refresh, alerts, playlists whose streams all fail, a part of the stack that doesn't answer. Each answer names the file it comes from and links to the fix.
- On a playlist's page, **Why?** next to a channel explains that channel: whether its stream works and how it has done, and where its guide comes from, or why it has none.
- In a terminal, in the kia folder, `kia doctor` gives the same answers, and `kia doctor channel <name>` explains one channel:

  ```sh
  docker compose exec kia python3 -m kia doctor
  docker compose exec kia python3 -m kia doctor channel "CNN"
  ```

  Without Docker, run `PYTHONPATH=src python3 -m kia doctor` instead.

None of these change anything.

## 2. Ask your own AI assistant

kia has an MCP server: a set of read-only tools an AI assistant can call, the same way it reads a file or searches the web. With it, you can ask Claude Code (or another assistant that speaks MCP) "why does ESPN have no guide?" and it looks at kia's record to answer.

It's off until you turn it on, and it answers only over HTTPS, to whoever has its token.

### Turn it on

In the kia folder, on the computer running kia:

```sh
docker compose exec kia python3 -m kia mcp-token
```

(Without Docker: `PYTHONPATH=src python3 -m kia mcp-token`.) It prints the server's address, its token, and the command for Claude Code. Keep the token private, like a password.

### Trust kia's certificate

The MCP server is on the [secure Dashboard](secure-page.md)'s port, and kia's own certificate isn't one other computers trust yet. Unless you [use your own certificate](secure-page.md#using-your-own-certificate), give Claude Code a copy of kia's:

1. Copy `tls/fullchain.pem` from the kia folder to the computer where you use Claude Code, for example to `~/kia-cert.pem`.
2. Start Claude Code with it trusted:

   ```sh
   NODE_EXTRA_CA_CERTS=~/kia-cert.pem claude
   ```

   To do that every time, add `export NODE_EXTRA_CA_CERTS=~/kia-cert.pem` to your shell's startup file (`~/.zshrc` on a Mac).

Without this, Claude Code reports that kia "failed to connect", and the error mentions a self-signed certificate.

### Add kia to Claude Code

Run the command `kia mcp-token` printed. It looks like this, with your address and token:

```sh
claude mcp add --transport http kia https://192.168.1.20:8743/api/mcp --header "Authorization: Bearer <token>"
```

Add `--scope user` to have kia in every project instead of only this folder. Then, in Claude Code, `/mcp` shows kia as connected, with six tools. Questions to try:

- "Is kia working? Anything I should fix?"
- "Why does Hogan's Heroes have no guide?"
- "Which playlists are failing, and what should I do about them?"
- "Is everything in my stack running?"

Claude Code asks before it uses a tool the first time; each one only reads.

### What the assistant can see

| Tool | What it returns |
|---|---|
| `kia_overview` | How the last refresh went, the counts, and anything that needs you |
| `kia_channel` | One channel's stream and guide, as **Why?** explains them |
| `kia_find_channels` | Channels whose name or ID contains some text |
| `kia_playlists` | The playlists kia checks and how their streams did, and the playlists it makes for people and devices |
| `kia_stack` | Each program running next to kia, and whether it answers |
| `kia_settings` | The settings kia is running with; passwords, tokens, and alert addresses only as set or not set |

That's the same as the Dashboard shows to anyone on your network. The tools can't change anything, and a user name or password inside any address is hidden. Your assistant sends what it reads to its own AI service to answer you, as it does with anything else you show it.

Channel names, guide titles, and guide sites' errors come from strangers, and someone could write instructions into one ("ignore your instructions and..."). kia tells the assistant to treat them as data. The tools can't act on them anyway, but don't run a command an answer suggests unless it's one kia's docs give.

### Manage it

- Show the token again: `kia mcp-token`.
- Replace it, if it got out: `kia mcp-token --reset`. The old one stops working at once; update Claude Code with the new one.
- Turn the server off: `kia mcp-token --off`.
- kia's log records each tool call and which address made it (`docker compose logs kia | grep MCP`).

A device that keeps sending a wrong token is slowed down, like a wrong Dashboard password, and each address can make 60 tool calls a minute. How it works, and why it's built this way: [security](../SECURITY.md#the-mcp-server).

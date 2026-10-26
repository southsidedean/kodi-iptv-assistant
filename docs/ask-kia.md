# Asking kia for help

kia keeps a record of every refresh: which streams worked, where each channel's guide came from, which guide sites failed, and what runs next to it. You can ask it about that record in two ways, the simplest first:

| | What you get | What it needs |
|---|---|---|
| [1. Ask kia why](#1-ask-kia-why) | Plain answers from the Dashboard or a terminal: what's wrong, where kia saw it, and what to do | Nothing. Works offline, free |
| [2. Ask your own AI assistant](#2-ask-your-own-ai-assistant) | Questions in your own words, answered by an assistant such as Claude Code that reads kia's record through kia's MCP server | An assistant that speaks MCP, and a few minutes to set it up |

Start with the first. The second asks the same questions in plain words, and can combine answers, such as "why is my guide emptier than last week?"

## 1. Ask kia why

- On the Dashboard, **Ask kia what's wrong** shows anything that needs you: a failed refresh, alerts, playlists whose streams all fail, a part of the stack that doesn't answer, and with Prometheus in the stack, a disk that's nearly full or failing. Each answer names the file it comes from and links to the fix.
- On a playlist's page, **Why?** next to a channel explains that channel: whether its stream works and how it has done, and where its guide comes from, or why it has none.
- In a terminal, in the kia folder, `kia doctor` gives the same answers, `kia doctor channel <name>` explains one channel, `kia doctor guide` the whole guide, `kia doctor pluto` Pluto TV, and `kia doctor setup` kia's own setup (its certificate, password, alerts, free space, and address). Three more cover Plex, Jellyfin, and the computer: `kia doctor plex` and `kia doctor jellyfin` say why they show no channels or guide, and `kia doctor hardware` checks the disks, memory, heat, and power (it needs the [observability part](cookbooks/observability.md)):

  ```sh
  docker compose exec kia python3 -m kia doctor
  docker compose exec kia python3 -m kia doctor channel "CNN"
  ```

  Without Docker, run `PYTHONPATH=src python3 -m kia doctor` instead.

To find a show in the guide, `kia find` searches kia's guide, and Plex's and Jellyfin's own guides when kia knows them (an antenna's channels in Plex, say). It looks a day ahead unless you say otherwise, and puts shows named for your words first:

```sh
docker compose exec kia python3 -m kia find Bears
docker compose exec kia python3 -m kia find --category Sports --hours 0
```

`--hours 0` means on now, and `--hours 168` looks a week ahead.

None of these change anything.

## 2. Ask your own AI assistant

kia has an MCP server: read-only tools an AI assistant can use to read kia's record and answer you in plain words. It works with assistants that run on your own computers, such as Claude Code, Gemini CLI, Claude Desktop, Codex, VS Code, and Cursor. Assistants that run in a company's cloud, such as ChatGPT and claude.ai, can't reach kia on your home network.

- On the computer running kia, the assistant starts kia's server itself (`kia mcp`), with nothing to turn on.
- From another computer, turn it on with `kia mcp-token`, trust kia's certificate, and add kia to your assistant. `kia mcp-token --for <assistant>` prints the setup with your address and token filled in.

[kia and AI assistants](mcp.md) has the steps for each assistant, what they can see, privacy and safety, and what to do when it doesn't connect.

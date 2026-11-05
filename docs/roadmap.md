# kia's roadmap

Where kia is and where it's headed. This is a guide to the direction, not a promise about dates
or order; plans change as the work gets real. For exactly what shipped in each release, see the
[changelog](../CHANGELOG.md).

## Where kia is today

kia tests free IPTV streams, builds a matching guide, and serves both to Kodi, Plex (through
Threadfin), Jellyfin, and other players, with a Dashboard to run it all. Docker Compose brings it
up, and you can add Plex, Jellyfin, graphs (Prometheus and Grafana), and more as parts of the
stack. It runs on your own network and keeps its settings and secrets to itself.

## Released

Newest first. The [changelog](../CHANGELOG.md) has the full detail for every version.

- 0.9.2 began real playback testing: kia's Pluto relay now counts and reports the pieces it
  sends without their sound, which some players wait on, and backups no longer carry old copies
  of `.env` back to where the media container can read them.
- 0.9.0 and 0.9.1 split kia into two containers with Docker. One serves the Dashboard and holds
  kia's password, tokens, and certificate key; the other reads strangers' streams and guide sites
  and sees only the few files it needs, so a stream that tricks one of the media tools has nothing
  to steal ([two containers](security-under-the-hood.md#two-containers)).
- 0.8.3 to 0.8.5 finished the identity and chat work. One login now covers the whole stack, with
  a real account per person and roles for who can change things, household accounts kept in a
  small store you manage, and an optional clean certificate from a public authority for browsers
  to trust with no warning. A small in-house chat sits behind the front door and answers
  questions about your stack from kia's own tools, staying on your network with the AI provider
  and the spending limits in your hands. The chat can also act on a few things for you now,
  refreshing the guide, testing a channel, adding a channel to Plex, or weighing a stack change,
  each one waiting for your approval. And a prompt you can hand to an AI assistant installs kia
  for you ([one login for the stack](front-door.md), [the chat page](chat.md)).
- 0.8.0 to 0.8.2 began accounts and one login: an optional front door that puts a single login in
  front of kia's pages, running offline with kia's own certificate ([one login for the
  stack](front-door.md)).
- 0.7.x made kia safer to share and improved the listings: an optional gateway in front of the
  MCP server, with its own key and a rate limit per device; a guard on kia's outbound
  connections; assistant tools that act only after you approve them on the Dashboard; and guide
  and radio improvements. A full independent review of the code closed the line.
- 0.6.x added AI help. `kia doctor` and the Dashboard's Why? and Ask kia buttons explain problems
  in plain words, `kia find` searches the guides for a show, and kia's MCP server lets an
  assistant such as Claude Code read it all ([asking kia for help](ask-kia.md)).
- 0.1 to 0.5 built the core: stream testing, the nightly guide, the Pluto TV relay, the
  Dashboard, playlists for people and devices, and Plex, Jellyfin, and Kodi.

## In progress

Nothing is in active development between releases right now. The next work is under Later, below.

## Later

- 0.9.x closes with a full independent review of the code and the docs, plus a step back to ask
  whether kia's design is still the right one now that the split is done.
- Windows support is on the list, not yet scheduled.
- Podman as an alternative to Docker. kia's two containers run under rootless Podman with the
  split intact, but Podman 4.9 (Ubuntu 24.04) and 5.7 (Ubuntu 26.04) both show kia every device
  as the same address, and kia's limits on password guessing and MCP calls count per address.
  It will be tried again on Podman 6, which adds a way to keep each device's address.
- A shared gateway for several kias, one always-on install's gateway sitting in front of a few
  others so devices that can't reach every stack directly still can, is an idea being weighed,
  not scheduled.

## How this page is kept

This roadmap is updated at each release, alongside the changelog. When something ships, it moves
from the plans above into [released](#released), and the changelog carries the specifics.

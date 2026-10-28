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

- 0.8.3 brings one login across the stack. The front door grows from one shared login into real
  accounts, each person with their own, and roles that decide who can change things. Household
  accounts live in a small store you can manage, and the same login can cover Grafana and the
  stack's other pages. A clean certificate from a public authority is an optional upgrade for
  anyone who wants browsers to trust it with no warning.
- 0.8.4 adds a small in-house chat: a page behind the front door that answers questions about
  your stack by using kia's own tools. It stays on your network, with the AI provider and the
  spending limits in your hands.

## Later

- 0.9.x puts a stronger boundary inside kia, splitting its one container so the parts that read
  untrusted streams can't reach its keys, password, or tokens even if one of those tools is ever
  tricked. It gets its own release with a design pass first, and a full review of both the code
  and the design at the end.
- Windows support is on the list, not yet scheduled.
- A shared gateway for several kias, one always-on install's gateway sitting in front of a few
  others so devices that can't reach every stack directly still can, is an idea being weighed,
  not scheduled.

## How this page is kept

This roadmap is updated at each release, alongside the changelog. When something ships, it moves
from the plans above into [released](#released), and the changelog carries the specifics.

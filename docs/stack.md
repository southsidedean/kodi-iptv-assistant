# Choosing the parts of kia's stack

kia can run other programs next to it: Plex, Jellyfin, Threadfin for a Plex elsewhere, and Prometheus and Grafana for graphs. You choose them with one line in `.env`, and `scripts/stack.sh` checks the choice for this computer and starts it:

```sh
KIA_STACK=plex,observability
```

```sh
scripts/stack.sh check     # check the choice; change nothing
scripts/stack.sh apply     # check, then start what's chosen and stop the rest
scripts/stack.sh           # what runs, and which dashboard panels have data
```

## The parts

| Part | What it adds |
|---|---|
| `plex` | Plex in the stack, with Threadfin as its tuner ([home TV box with Plex](cookbooks/plex-box.md)) |
| `plex-tuner` | Threadfin as the tuner for a Plex on another computer ([Plex](plex.md)) |
| `plex-tuner-2` | A second Threadfin, for more than 480 channels in Plex; with `plex` or `plex-tuner` |
| `jellyfin` | Jellyfin in the stack ([home TV box with Jellyfin](cookbooks/jellyfin-box.md)) |
| `observability` | Prometheus, Grafana, and cAdvisor, and on Linux node-exporter for the computer's own numbers ([graphs](cookbooks/observability.md)) |
| `observability-gpu` | The graphics card on the Hardware dashboard: an NVIDIA card, on Linux |
| `observability-disks` | Each disk's health on the Hardware dashboard: Linux |

Plex and Jellyfin can run side by side. With `KIA_STACK=` (empty), kia runs with its web server only, for Kodi and other players. `scripts/stack.sh list` shows the parts and the presets.

## Presets

A preset sets `KIA_STACK` for you, for one of the common setups; the installer offers them too:

```sh
scripts/stack.sh apply --preset plex-box
```

| Preset | `KIA_STACK` |
|---|---|
| `kodi` | (empty): kia and its web server |
| `plex-box` | `plex` |
| `plex-elsewhere` | `plex-tuner` |
| `jellyfin-box` | `jellyfin` |
| `everything` | `plex,jellyfin,observability` |

They're in `stacks/` in the kia folder. Add your own parts to `KIA_STACK` afterwards, such as `observability-gpu` on a computer with an NVIDIA card.

## What check looks at

`check` (and `apply`, before it changes anything) stops on a *problem* and points out a *warning*:

- **Problems:** a name it doesn't know; `plex` with `plex-tuner` (Plex already brings its tuner); `observability-gpu` without an NVIDIA card and NVIDIA's Container Toolkit, or either Hardware part on a Mac; a media folder that's missing or can't be read; a port another program already uses.
- **Warnings:** settings a part needs and doesn't have yet, such as `KIA_PLEX_ADVERTISE` or a claim code for Plex's first start; on a Mac, a database in a folder rather than a [Docker volume](configuration.md#on-a-mac), or no node-exporter from Homebrew; a computer with less than 6 GB of memory running more than one of Plex, Jellyfin, and the graphs.
- **Hints:** on Linux, an NVIDIA card or disks it could show on the Hardware dashboard.

It also works out the Compose profiles, the names `compose.yaml` groups its containers under: on Linux, `observability` adds kia's node-exporter unless another one already answers on port 9100.

## What apply does

1. Runs `check`, and stops on a problem.
2. Asks first if someone is watching Pluto TV, Plex, or Jellyfin through kia, and if parts that run now would stop because `KIA_STACK` leaves them out (`--yes` skips both).
3. Keeps a copy of `.env` in `state/env.before-stack-<date>`, then sets `COMPOSE_PROFILES` from `KIA_STACK`, and `KIA_PLEX_URL` or `KIA_JELLYFIN_URL` for a server in the stack if they're empty.
4. Runs `docker compose up -d`, and stops and removes the containers of parts you took out.

Running it again with nothing changed restarts nothing.

## Without KIA_STACK

Before `KIA_STACK`, `COMPOSE_PROFILES` in `.env` chose what runs, and it still does while `KIA_STACK` is empty or missing; [configuration](configuration.md) lists the profiles. `scripts/stack.sh` then shows what runs and says so, and `check` and `apply` change nothing. Once you set `KIA_STACK`, `apply` sets `COMPOSE_PROFILES` for you, so change `KIA_STACK` instead.

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

A preset sets `KIA_STACK` for you, for one of the common setups. The installer offers them, and the Dashboard's Stack page shows each one as a card:

```sh
scripts/stack.sh apply --preset plex-box
```

| Preset | Card | `KIA_STACK` | Needs |
|---|---|---|---|
| `kodi` | Kodi and other players | (empty): kia and its web server | Nothing more than kia itself |
| `plex-box` | Plex TV box | `plex` | About 2 GB more memory, and a free Plex account |
| `plex-box-graphs` | Plex TV box with graphs | `plex,observability` | About 2.5 GB more memory, and a free Plex account |
| `plex-elsewhere` | Threadfin for your Plex | `plex-tuner` | A Plex you already run |
| `jellyfin-box` | Jellyfin TV box | `jellyfin` | About 2 GB more memory |
| `jellyfin-box-graphs` | Jellyfin TV box with graphs | `jellyfin,observability` | About 2.5 GB more memory |
| `everything` | Everything | `plex,jellyfin,observability` | About 5 GB more memory (8 GB or more in all), and a free Plex account |

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

`scripts/stack.sh check --stack plex,observability` checks a choice without changing `.env`, and `apply --stack plex,observability` sets `KIA_STACK` to it first.

## Changing the stack from the Dashboard

The Dashboard's **Stack** page can check and apply a new choice for you. kia can't do that itself: its container has no access to Docker, on purpose. A small service on the computer, **kia-stack**, does it instead. It runs `scripts/stack.sh watch` as you, and carries out a check or an apply of the parts you choose: what you could do with the script yourself. Install it once, in the kia folder:

```sh
scripts/install-service.sh --stack
```

(Or add `--stack-service` when you run the installer.) On Linux it's a systemd user service (`systemctl --user status kia-stack`), which runs only while you're logged in unless you let it keep running: `sudo loginctl enable-linger "$(id -un)"`, once. On a Mac, it's a launchd agent (log: `work/logs/kia-stack.log`). `scripts/install-service.sh --stack --uninstall` removes it. Without it, the Stack page shows the command to run instead.

On the page, choose a preset's card, or the parts yourself, then **Check**, then **Apply**:

- **Apply** comes on only after a **Check** of the same choice found no problems. It asks first if someone is watching or if parts that run now would stop, since the service itself applies without asking.
- Plex's first start needs a claim code, which links it to your Plex account. With Plex ticked and not running yet, the page asks for one: get it at [plex.tv/claim](https://plex.tv/claim) right before Apply, since it works for 4 minutes.
- Plex's playlist: if the check says there's no playlist named `plex` yet, the page offers to make it, with all the TV channels when they fit in Plex's 480, or sends you to Playlists to pick fewer.
- After **Apply**, the page says what each new part still needs: finishing Jellyfin's setup (do it right away), adding Live TV to Plex (a button does it now; otherwise kia does it within 4 hours), and where Grafana is.
- The addresses Plex and Jellyfin apps use (`KIA_PLEX_ADVERTISE`, `KIA_JELLYFIN_ADVERTISE`) are filled in from `KIA_PUBLIC_URL` when they're empty, and `scripts/stack.sh apply --claim CODE` sets the claim code from the terminal.

How it works: when you choose **Check** or **Apply** on the secure Dashboard, after the password, kia leaves the request in `state/stack-request`. Within a few seconds the service moves it into its own folder, `stack-service/` in the kia folder, runs `scripts/stack.sh check --stack ...` or `apply --stack ... --yes`, and leaves what the script said in `stack-service/result` for the page to show. Every 30 seconds it also writes what each container is doing to `stack-service/containers.tsv`. kia can read that folder but not write to it. A request has to be `check` or `apply` with part names only, and less than 5 minutes old; anything else is refused without running. [Security](../SECURITY.md#the-kia-stack-service) explains what the service guards against.

## Without KIA_STACK

Before `KIA_STACK`, `COMPOSE_PROFILES` in `.env` chose what runs, and it still does while `KIA_STACK` is empty or missing; [configuration](configuration.md) lists the profiles. `scripts/stack.sh` then shows what runs and says so, and `check` and `apply` change nothing. Once you set `KIA_STACK`, `apply` sets `COMPOSE_PROFILES` for you, so change `KIA_STACK` instead.

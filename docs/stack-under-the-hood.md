# The stack under the hood

What happens, file by file, when you choose what runs next to kia: from one line in `.env` to Docker starting Plex, and from a click on the Dashboard's Stack page to the same thing. To just use it, see [your stack from the Dashboard](stack-dashboard.md) or [choosing the parts of kia's stack](stack.md). Read this one to see how it works, or when something doesn't add up.

## The pieces

| Piece | Where | What it does |
|---|---|---|
| `KIA_STACK` | `.env` | Your choice: part names, with commas between, such as `plex,observability` |
| The presets | `stacks/*.env` | Ready-made choices. Each sets `KIA_STACK`, with a title, what it needs, and its place on the page (`# title:`, `# needs:`, `# order:`) |
| `compose.yaml` | the kia folder | Every container kia's stack can run, each under a Compose *profile*. Docker starts the containers of the profiles named in `COMPOSE_PROFILES` |
| `scripts/stack.sh` | the kia folder | Turns `KIA_STACK` into `COMPOSE_PROFILES`, checks the choice for this computer, and starts and stops containers |
| The kia-stack service | on the computer, as you | `scripts/stack.sh watch`: carries out the Stack page's requests |
| kia | in its container | Serves the Stack page, leaves requests for the service, and knocks on each program to fill in the Dashboard's **Your stack** tiles |

## From parts to profiles

Parts are the names you choose. Profiles are the names `compose.yaml` groups its containers under. `stack.sh` maps one to the other, so you never have to know the profiles:

| Part | Compose profiles | Containers |
|---|---|---|
| (always) | none | `kia`, `web` (the web server) |
| `plex` | `plex`, `plex-server` | Threadfin, Plex |
| `plex-tuner` | `plex` | Threadfin |
| `plex-tuner-2` | `plex-2` | a second Threadfin |
| `jellyfin` | `jellyfin-server` | Jellyfin |
| `observability` | `monitoring`, and on Linux `monitoring-node` | Prometheus, Grafana, cAdvisor, and node-exporter |
| `observability-gpu` | `monitoring-gpu` | the graphics card exporter |
| `observability-disks` | `monitoring-disks` | the disk health exporter |

`monitoring-node` is left out when something else already answers on port 9100, such as a node-exporter you run yourself. Prometheus then reads that one. On a Mac it's always left out: Docker runs in a Linux machine of its own there, so kia's node-exporter would describe that machine, not the Mac. Homebrew's node-exporter on the Mac takes its place.

The profile `plex` is only Threadfin, while the part `plex` is Plex and Threadfin. If you set `COMPOSE_PROFILES` by hand before `KIA_STACK` existed, use the table in [moving to KIA_STACK](stack.md#without-kia_stack).

## Check, step by step

`scripts/stack.sh check` (and `apply`, before it changes anything) reads the choice from `KIA_STACK`, or from `--stack` or `--preset` without touching `.env`. Then it:

1. Maps each part to its profiles. An unknown name is a problem.
2. Checks the parts against each other: `plex` with `plex-tuner` (Plex already brings its tuner), `plex-tuner-2` without one of them, or a Hardware part without `observability`.
3. Checks them against this computer: an NVIDIA card and NVIDIA's Container Toolkit for `observability-gpu`, Linux for either Hardware part, a media folder it can read, and, for Plex, Jellyfin, Grafana, and Prometheus when they aren't running yet, that nothing else answers on their port. With less than 6 GB of memory and two or more of Plex, Jellyfin, and the graphs, it warns that memory may run short.
4. Looks for what the parts need: kia's `plex` playlist for Threadfin (in `output/`), a claim code for Plex's first start, and the addresses Plex and Jellyfin apps use. A missing playlist is a problem; the rest are warnings.
5. Prints the result: `KIA_STACK`, the `COMPOSE_PROFILES` it makes, the profiles that run now and that the choice leaves out ("Stops what runs now"), and a count of problems and warnings.

It exits with 1 when there's a problem, and with 0 otherwise. Warnings never stop it.

## Apply, step by step

`scripts/stack.sh apply` runs the check, and stops on a problem. Then:

1. It asks, unless you add `--yes`. It reads kia's `/metrics` to see whether anyone is watching Pluto TV, Plex, or Jellyfin, or whether a refresh is running. Then it lists the profiles that would stop. Each question defaults to no.
2. It keeps a copy of `.env` in `state/env.before-stack-<date>`, readable only by you. `set -C` makes the shell refuse to write through anything already at that name, such as a link.
3. It writes `.env`. It sets `KIA_STACK` (only now, after the check and the questions, so a refused or cancelled apply leaves `.env` as it was). Then it sets `PLEX_CLAIM` from `--claim`, and fills in whichever of these are empty:
   - `KIA_PLEX_ADVERTISE` and `KIA_JELLYFIN_ADVERTISE`, from the host in `KIA_PUBLIC_URL` (not `localhost`, and nothing with characters an address can't have)
   - `KIA_PLEX_URL` and `KIA_JELLYFIN_URL`, set to the containers' own addresses
   - `COMPOSE_PROFILES`, from the parts

   Each change rewrites `.env` through a temporary file made with `umask 077`, so it stays readable only by you.
4. It starts what's chosen: `docker compose up -d`. Compose recreates any container whose settings changed. kia's container gets the profiles as `KIA_COMPOSE_PROFILES`, so a new choice restarts kia too.
5. It stops what isn't chosen: every container of kia's stack, in any state, that the new profiles don't include is stopped and removed (`docker compose rm --stop --force`). Their data folders stay.

Running it again with nothing changed restarts nothing.

## The Stack page and the kia-stack service

kia's container has no Docker socket, on purpose: kia runs other people's code (iptv-org's guide downloaders) and opens streams from strangers, and access to Docker is as good as full control of the computer. So the Stack page can't start anything. It leaves a request in a file instead, and the kia-stack service, running on the computer as you, carries it out.

### A request, from click to answer

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/stack-request-dark.svg">
  <img alt="A request from the Stack page, step by step. The page sends Check or Apply to kia with the password, over HTTPS. kia checks the parts and claim code, then leaves state/stack-request with the id, action, and parts. The kia-stack service looks every 3 seconds, renames the request into stack-service/, and checks it again. It answers running, runs scripts/stack.sh check or apply, gets back what the script printed, then writes containers.tsv and the finished result. The page asks kia every few seconds and gets the answer for its id." src="../images/diagrams/stack-request-light.svg">
</picture>

1. You choose **Check** or **Apply** on the secure page. The browser sends your choice to kia's API with the password, over HTTPS only ([API](api.md)).
2. kia checks the parts and any claim code. If the service is still working on a request, kia refuses a new one. Then kia writes `state/stack-request`, readable only by its owner, through a temporary file renamed into place, so the service never sees half a request:

   ```ini
   id=3f9a0c51d2e8b746
   action=apply
   stack=plex,observability
   claim=claim-xxxxxxxxxxxxxxxxxxxx
   ```

   The id is random, so the page knows which answer is to its own request. `claim=` comes only with an apply that starts Plex for the first time.
3. Every 3 seconds the service looks for that file. It renames it into its own folder, `stack-service/request`, and reads only the first 4 KB of what arrived. It checks each value again:
   - The action is `check` or `apply`.
   - The parts are lowercase names with commas, 200 characters at most.
   - The id is letters, digits, and dashes, 64 characters at most.
   - The claim code is `claim-` and letters, digits, dashes, and underscores, 80 characters at most, and comes only with an apply.
   - The request is less than 5 minutes old.

   A request that fails any of these is refused without running anything. The service deletes its copy of the request as soon as it has read it.
4. It answers `running` straight away, then runs `scripts/stack.sh check --stack ...` or `apply --stack ... --yes`. An apply doesn't stop to ask about viewers, since nobody is at a terminal to answer. The page asks before it sends the request instead.
5. It writes the containers' states to `containers.tsv` first, so the page shows what runs now, then its answer to `stack-service/result`: a few lines, a blank line, then the last 200 lines the script printed:

   ```ini
   id=3f9a0c51d2e8b746
   action=apply
   state=finished
   exit=0
   finished=2026-10-24T18:05:12Z

   KIA_STACK=plex,observability
   ...
   ```

6. The page asks kia for the answer every few seconds, and shows it when its id matches. After an apply that restarts kia, the page keeps asking until kia answers again.

### What the service writes

Everything it writes stays in `stack-service/` in the kia folder. kia sees that folder read-only, like the rest of the kia folder.

| File | What's in it |
|---|---|
| `request` | The request it's working on, until it's done |
| `result` | Its answer to the last request (above) |
| `containers.tsv` | Each container, one per line: its service, state, health, and what Docker says. Rewritten every 30 seconds, and after each request |
| `watch.out` | What the script printed for the last request |
| `watch.pid` | The service's process id, so a second copy refuses to start and can't carry out the same request twice |

### How the page knows the service is there

The page treats the service as running while `containers.tsv` is less than 90 seconds old, or while the last answer says `running` and is less than 30 minutes old (a first apply can spend that long downloading). Otherwise **Check** and **Apply** are greyed out, and the page shows the command to run in the kia folder instead. kia also reports this in `/metrics` as `kia_stack_service_up`.

### Why it's built this way

Assume someone has taken over kia's container. They can write anything in `state/`, links and folders included. So the service never writes, reads, or follows anything there. It only renames the request away, and a rename moves a link itself, never what it points to. The most a taken-over kia can do through the service is ask for what you could do with `scripts/stack.sh apply --yes`: start or stop the parts listed above. [Security](../SECURITY.md#the-kia-stack-service) has the rest.

## The Your stack tiles

The tiles at the top of the Dashboard don't come from the service. Every minute, kia knocks on each program's port over Compose's network (Threadfin on 34400, Plex on 32400, Jellyfin on 8096, Grafana on 3000, and so on). A program that accepts the connection is green. For node-exporter, kia asks Prometheus instead, since it may run outside the stack (Homebrew's, on a Mac). kia writes the result to `output/stack.json`, at `http://<your address>:8780/stack.json`.

A knock can tell a running program from one that doesn't answer. It can't tell one that's starting from one that has stopped. The Stack page's **What runs now** can, because it shows what Docker says, through the service.

## The Plex extras

- **The claim code** goes from the page to the request (`claim=`), to `stack.sh apply --claim`, to `PLEX_CLAIM` in `.env`. Plex reads it on its first start only, to link itself to your account. Each step checks it, it never appears in what the script prints, and it works for 4 minutes.
- **The playlist.** When the check's output says there's no playlist named `plex`, the page asks kia how many TV channels it has. With 480 or fewer, its button saves a playlist named `plex` with all of them and **For Plex through Threadfin** ticked, the same as making it on the Playlists page. kia writes `output/plex.m3u` right away, so the next Check finds it.
- **Add Live TV to Plex now** runs `kia plex-tuners` inside kia, one at a time: the same step kia runs by itself after each guide refresh. It needs `KIA_PLEX_URL`, which apply sets for Plex in the stack.

## Without the service

Everything on the Stack page is `scripts/stack.sh` underneath, so the terminal does the same:

```sh
scripts/stack.sh check --stack plex,observability            # what Check does
scripts/stack.sh apply --stack plex,observability --claim claim-xxxx   # what Apply does
scripts/stack.sh                                             # what runs
```

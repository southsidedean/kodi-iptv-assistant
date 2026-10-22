# Graphs for your TV stack: Prometheus and Grafana

kia's Dashboard tells you how things are now. Add Prometheus and Grafana, and you also see how they've been: whether last night's refresh took longer than usual, when a channel source lost half its streams, how busy Threadfin was during the game, and who was watching what on Plex. They run in kia's stack, set up from `.env`, with three dashboards ready to use. Prometheus collects the numbers and keeps them; Grafana draws the graphs. Two small helpers measure the rest: cAdvisor each container, and node-exporter the computer itself.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../images/diagrams/observability-dark.svg">
  <img alt="kia (refreshes, channels, Pluto TV, Plex), cAdvisor (each container), and node-exporter (the computer) feed Prometheus, which keeps 30 days. Grafana shows the kia, TV stack, and Plex dashboards, in your browser or from the Dashboard's Graphs button." src="../../images/diagrams/observability-light.svg">
</picture>

| Dashboard | What it shows |
|---|---|
| **kia** | The nightly refresh (OK or failed, how long it took), the guide's age, channels and guide coverage, working and dead streams per source, channels per playlist, the Pluto TV relay's viewers and problems, and kia's own processor and memory use |
| **TV stack** | Processor, memory, and network for each container (kia, the web server, the Threadfins, Plex in the stack), and the computer's processor, memory, disk, and network |
| **Plex** | Who's watching now and what (Live TV, movies, episodes), whether Plex copies or converts the video, and players away from home |

![The kia dashboard in Grafana: the last refresh OK, the guide's age, channel counts, and graphs of channels, guide coverage, and streams per source](../../images/cookbooks/observability-kia.png)

## What you need

- kia running with Docker ([installing kia](../install.md)), or the [home TV box](plex-box.md).
- About 1 GB of disk for 30 days of numbers, and about half a GB of memory, mostly Grafana's (up to 1 GB while it sets itself up the first time).

## 1. Turn it on

In the kia folder, add `monitoring` to `COMPOSE_PROFILES` in `.env`. You can also choose a password for Grafana; without one, Grafana makes one up on its first start (step 2 says where to find it):

```sh
COMPOSE_PROFILES=monitoring
KIA_GRAFANA_PASSWORD=choose-a-password
```

Keep the profiles you already have and put `monitoring` on the end: with Threadfin for Plex, that's `COMPOSE_PROFILES=plex,monitoring`.

The computer's own numbers come from node-exporter on port 9100. If the computer doesn't run it already, add `monitoring-node` as well: `COMPOSE_PROFILES=monitoring,monitoring-node`. (On Ubuntu or Debian, `systemctl status prometheus-node-exporter` shows whether it's there.)

Then start it:

```sh
docker compose up -d
```

## 2. Open Grafana

Go to `http://192.168.1.20:8788`, with your kia's address, and sign in as `admin` with the password from step 1. If you didn't choose one, it's in `monitoring/grafana/admin-password.txt` in the kia folder. The kia dashboard opens first; the buttons at the top lead to the other two. kia's own Dashboard also gets a **Graphs** button that opens it.

The graphs start when Prometheus does, so they fill in over the following hours, and the refreshes over the following nights.

![The TV stack dashboard: processor, memory, and network for each container, and the computer's own numbers](../../images/cookbooks/observability-stack.png)

## 3. Plex (optional)

The Plex dashboard reads Plex through kia, with the same settings as [`kia plex-tuners`](../plex.md#4-add-threadfin-to-plex): `KIA_PLEX_URL`, and `KIA_PLEX_TOKEN` for a Plex outside the stack. With Plex in the stack ([home TV box](plex-box.md)), `KIA_PLEX_URL=http://plex:32400` is enough. If those are already in `.env`, there's nothing to do.

Plex installed outside Docker isn't a container, so its processor use shows in the TV stack dashboard's computer section, not per container.

## Adding your own

**Other things to collect.** Put a file in `monitoring/targets/` in the kia folder, one per job. For example, a Plex exporter on port 9000 of this computer, `monitoring/targets/plex.yml`:

```yaml
- targets: ["host.docker.internal:9000"]
  labels:
    job: plex-exporter
```

Prometheus picks new files up within a few minutes, without a restart.

**Your own dashboards.** Put Grafana dashboard JSON files in `monitoring/dashboards/`; they appear in Grafana's *Local* folder. Edits you make to kia's three dashboards in Grafana aren't kept, because they're reloaded from kia's files; save a copy (*Save as*) to change one.

## Good to know

- **Ports.** Grafana is on 8788 (`KIA_GRAFANA_PORT`). Prometheus is on 8789 (`KIA_PROMETHEUS_PORT`), open to this computer only, because its page has no password. Both stay off the common ports 3000 and 9090, which other programs often use.
- **How long it keeps.** 30 days (`KIA_PROMETHEUS_RETENTION`, such as `90d`). The numbers are in `monitoring/prometheus`, and Grafana's settings in `monitoring/grafana`, in the kia folder.
- **kia's numbers on their own.** kia publishes them at `http://192.168.1.20:8780/metrics` for any Prometheus you already have; [metrics](../metrics.md) lists them.
- **Turning it off.** Remove `monitoring` (and `monitoring-node`) from `COMPOSE_PROFILES`, then `docker compose rm -sf prometheus grafana cadvisor node-exporter`. The numbers stay in `monitoring/` until you delete them.

## If something's wrong

**The computer's graphs are empty.** Nothing is answering on port 9100: add `monitoring-node` to `COMPOSE_PROFILES` (step 1). If a firewall runs on the computer, it has to let Docker's network reach port 9100.

**Grafana won't start: "address already in use".** Something else has port 8788. Set `KIA_GRAFANA_PORT` to another one, such as `8798`, and run `docker compose up -d`.

**The Plex dashboard says "Not answering".** kia can't reach Plex, or the token is wrong. Check `KIA_PLEX_URL` and `KIA_PLEX_TOKEN` in `.env`; the [Plex guide](../plex.md#4-add-threadfin-to-plex) shows how to find a token. After a change, run `docker compose up -d`.

**Forgot Grafana's password, or changed `KIA_GRAFANA_PASSWORD`.** Grafana reads that setting (or makes `monitoring/grafana/admin-password.txt`) only the first time it starts. Set the one you want, then run `docker compose exec grafana grafana cli admin reset-admin-password "$(grep ^KIA_GRAFANA_PASSWORD= .env | cut -d= -f2-)"`.

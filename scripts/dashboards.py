#!/usr/bin/env python3
"""Write the Grafana dashboards for kia's monitoring profile into deploy/monitoring/dashboards/.

Run it after changing a panel here: python3 scripts/dashboards.py
tests/test_docs.py checks that the JSON files match what this script writes.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deploy" / "monitoring" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
SVC = "container_label_com_docker_compose_service"
# Memory in use, as a fraction. Linux reports what's available; macOS reports its kinds of
# memory instead, and Activity Monitor's "Memory Used" is app memory (internal, less
# purgeable), wired, and compressed.
MEMORY_USED = ("(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) or ((node_memory_internal_bytes"
               " - node_memory_purgeable_bytes + node_memory_wired_bytes + node_memory_compressed_bytes)"
               " / node_memory_total_bytes)")
PROJECT = 'container_label_com_docker_compose_project="$project"'
# What a panel says when there's nothing to show, instead of Grafana's "No data".
GPU_OFF = "Needs observability-gpu in KIA_STACK, or the monitoring-gpu profile (an NVIDIA card, on Linux)"
DISKS_OFF = "Needs observability-disks in KIA_STACK, or the monitoring-disks profile (Linux)"
NO_SENSORS = "No sensors to read here (macOS and virtual machines report none)"
NO_UPS = "No UPS or battery node-exporter can see"
NO_NODE = "No numbers from node-exporter on port 9100"
NO_CONTAINERS = "No containers seen: is cAdvisor running?"
# The same, short enough for a small tile.
GPU_OFF_TILE, DISKS_OFF_TILE, NO_UPS_TILE = "Needs observability-gpu", "Needs observability-disks", "No UPS"


class Board:
    def __init__(self, uid: str, title: str, description: str, no_value: str) -> None:
        """no_value: what a panel says when its source sends nothing, unless it has a note of its own."""
        self.uid, self.title, self.description, self.no_value = uid, title, description, no_value
        self.panels: list[dict] = []
        self.x = self.y = self.row_height = 0
        self.variables: list[dict] = []

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_height
            self.row_height = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_height = max(self.row_height, h)
        return pos

    def row(self, title: str) -> None:
        if self.x:
            self.x, self.y = 0, self.y + self.row_height
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})  # fmt: skip
        self.y += 1
        self.row_height = 0

    def _panel(self, kind: str, title: str, description: str, targets: list[tuple[str, str]], w: int, h: int,
               unit: str = "short", **extra) -> dict:  # fmt: skip
        panel = {
            "id": len(self.panels) + 1,
            "type": kind,
            "title": title,
            "description": description,
            "datasource": DS,
            "gridPos": self._place(w, h),
            "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": expr, "legendFormat": legend}
                        for i, (expr, legend) in enumerate(targets)],
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "options": {},
        }
        panel.update(extra)
        self.panels.append(panel)
        return panel

    def stat(self, title: str, description: str, expr: str, unit: str = "short", w: int = 4,
             mappings: list | None = None, thresholds: list | None = None, decimals: int | None = None,
             no_value: str | None = None) -> None:  # fmt: skip
        p = self._panel("stat", title, description, [(expr, "")], w, 4, unit)
        d = p["fieldConfig"]["defaults"]
        if no_value:
            d["noValue"] = no_value
        if mappings:
            d["mappings"] = mappings
        steps = thresholds or [{"color": "blue", "value": None}]
        if steps[0]["value"] is None:  # real values start their color scale; no value at all stays plain:
            # no background on a tile colored by its thresholds, the usual text color on the others.
            steps = [{"color": "transparent" if thresholds else "text", "value": None}, {**steps[0], "value": -1e9}, *steps[1:]]
        d["thresholds"] = {"mode": "absolute", "steps": steps}
        if decimals is not None:
            d["decimals"] = decimals
        p["options"] = {"colorMode": "background" if thresholds else "value", "graphMode": "none", "textMode": "value",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}  # fmt: skip

    def graph(self, title: str, description: str, targets: list[tuple[str, str]], unit: str = "short", w: int = 12,
              stack: bool = False, h: int = 8, no_value: str | None = None) -> None:  # fmt: skip
        p = self._panel("timeseries", title, description, targets, w, h, unit)
        # Every drawing setting: Grafana doesn't fill in the ones a dashboard leaves out.
        p["fieldConfig"]["defaults"]["custom"] = {
            "drawStyle": "line", "lineInterpolation": "linear", "lineWidth": 2, "fillOpacity": 25 if stack else 8,
            "gradientMode": "none", "showPoints": "never", "pointSize": 4, "spanNulls": False, "axisPlacement": "auto",
            "axisBorderShow": False, "barAlignment": 0, "insertNulls": False,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "scaleDistribution": {"type": "linear"}, "thresholdsStyle": {"mode": "off"},
        }  # fmt: skip
        if no_value:
            p["fieldConfig"]["defaults"]["noValue"] = no_value
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}}

    def table(self, title: str, description: str, expr: str, columns: list[str], w: int = 24, h: int = 8,
              no_value: str | None = None) -> None:  # fmt: skip
        p = self._panel("table", title, description, [(expr, "")], w, h)
        if no_value:
            p["fieldConfig"]["defaults"]["noValue"] = no_value
        p["targets"][0].update({"format": "table", "instant": True})
        p["transformations"] = [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True, "__name__": True, "instance": True, "job": True},
            "indexByName": {c: i for i, c in enumerate(columns)},
            "renameByName": {c: c.capitalize() for c in columns}}}]  # fmt: skip

    def columns(self, title: str, description: str, key: str, columns: list[tuple[str, str, str]], w: int = 24,
                h: int = 8, no_value: str | None = None) -> None:  # fmt: skip
        """A table with a row per `key` label (a disk, an array) and a column per query:
        columns are (name, expr, unit)."""
        p = self._panel("table", title, description, [(expr, name) for name, expr, _ in columns], w, h)
        if no_value:
            p["fieldConfig"]["defaults"]["noValue"] = no_value
        for target in p["targets"]:
            target.update({"format": "table", "instant": True})
        p["transformations"] = [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True},
                "indexByName": {key: 0, **{f"Value #{chr(65 + i)}": i + 1 for i in range(len(columns))}},
                "renameByName": {key: key.capitalize(), **{f"Value #{chr(65 + i)}": name for i, (name, _, _) in enumerate(columns)}}}},
            {"id": "filterFieldsByName", "options": {"include": {"names": [key.capitalize(), *[n for n, _, _ in columns]]}}},
        ]  # fmt: skip
        # A cell the source doesn't report (a virtual disk has no SMART status) says n/a; the panel's
        # note is for when there's nothing at all.
        p["fieldConfig"]["overrides"] = [
            {"matcher": {"id": "byName", "options": name}, "properties": [{"id": "unit", "value": unit}, {"id": "noValue", "value": "n/a"}]}
            for name, _, unit in columns
        ]  # fmt: skip

    def json(self) -> dict:
        for p in self.panels:
            if p["type"] != "row":
                p["fieldConfig"]["defaults"].setdefault("noValue", self.no_value)
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["kia"],
            "timezone": "browser",
            "schemaVersion": 39,
            "version": 1,
            "editable": True,
            "graphTooltip": 1,
            "refresh": "1m",
            "time": {"from": "now-24h", "to": "now"},
            "links": [{"title": "kia's dashboards", "type": "dashboards", "tags": ["kia"], "asDropdown": False}],
            "templating": {"list": self.variables},
            "panels": self.panels,
        }


OK_FAILED = [{"type": "value", "options": {
    "0": {"text": "Failed", "color": "red"}, "1": {"text": "OK", "color": "green"}, "2": {"text": "Refreshing", "color": "blue"}}}]  # fmt: skip
GREEN = [{"color": "green", "value": None}]


def kia() -> Board:
    b = Board("kia", "kia", "kia's refreshes, channels, guide, sources, and Pluto TV relay (docs/metrics.md).",
              "No numbers from kia yet (its first refresh may still be running)")  # fmt: skip
    b.row("Now")
    b.stat("Last refresh", "The nightly refresh: OK, failed, or running now.",
           # While a refresh runs there's no last result yet, so the running one comes first.
           "(kia_refresh_running == 1) * 2 or kia_last_refresh_success", mappings=OK_FAILED,
           thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}, {"color": "blue", "value": 2}])  # fmt: skip
    b.stat("Refresh took", "How long the last nightly refresh took.", "kia_last_refresh_duration_seconds", "s")
    b.stat("Guide age", "Since the guide was last rebuilt. kia refreshes it every few hours.",
           "time() - kia_guide_updated_time_seconds", "s",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 8 * 3600}, {"color": "red", "value": 26 * 3600}])  # fmt: skip
    b.stat("TV channels", "TV channels in the full playlist.", 'kia_channels{kind="tv"}')
    b.stat("With guide", "Channels with guide data.", "kia_channels_with_guide / on(instance) sum by (instance) (kia_channels)",
           "percentunit", decimals=0)  # fmt: skip
    b.stat("Pluto channels", "Pluto TV channels kia is playing now.", "kia_pluto_channels_playing")
    b.row("Channels and guide")
    b.graph("Channels", "Channels in the full playlist, and how many have guide data.",
            [('kia_channels{kind="tv"}', "TV"), ('kia_channels{kind="radio"}', "Radio"), ("kia_channels_with_guide", "With guide")])  # fmt: skip
    b.graph("Guide coverage", "TV channels with a full guide, part of one, or only their name.",
            [("kia_guide_coverage_channels", "{{level}}")], stack=True)  # fmt: skip
    b.graph("Working streams per source", "Streams that played at the last check, for each channel source.",
            [('kia_source_streams{status="ok"}', "{{source}}")])  # fmt: skip
    b.graph("Dead streams per source", "Streams that didn't play at the last check.",
            [('kia_source_streams{status="dead"}', "{{source}}")])  # fmt: skip
    b.graph("Playlists", "TV channels in each playlist.", [('kia_playlist_channels{kind="tv"}', "{{playlist}}")], w=24)
    b.row("Pluto TV relay")
    b.graph("Viewers per channel", "Players watching each Pluto TV channel through kia (Threadfin counts as one).",
            [("kia_pluto_viewers", "{{name}}")], stack=True, no_value="No Pluto TV channels watched in this time")  # fmt: skip
    b.graph("Video passed on", "Pluto TV video kia sends to players.", [("rate(kia_pluto_bytes_total[5m]) * 8", "bits per second")], "bps")
    b.graph("Problems per hour", "Pieces kia couldn't pass on, times Pluto went back in a stream, and falls back to the shared stream.",
            [("increase(kia_pluto_errors_total[1h])", "Errors"), ("increase(kia_pluto_rewinds_total[1h])", "Rewinds"),
             ("increase(kia_pluto_fallbacks_total[1h])", "Fallbacks")], w=24)  # fmt: skip
    b.row("kia itself")
    b.graph("Processor", "Share of one processor core: kia itself, and the programs it runs (ffprobe while checking, ffmpeg for Pluto, the guide grabber).",
            [("rate(kia_process_cpu_seconds_total[5m])", "kia"), ("rate(kia_children_cpu_seconds_total[5m])", "Programs kia runs")],
            "percentunit", stack=True)  # fmt: skip
    b.graph("Memory", "Memory kia uses.", [("kia_process_resident_memory_bytes", "kia")], "bytes")
    return b


def stack() -> Board:
    b = Board("kia-stack", "TV stack", "Each container (kia, web, Threadfin, Plex) and the computer they run on.", NO_NODE)
    b.variables.append({"name": "project", "label": "Compose project", "type": "query", "datasource": DS,
                        "query": {"query": "label_values(container_last_seen, container_label_com_docker_compose_project)", "refId": "P"},
                        "refresh": 2, "current": {"text": "kodi-iptv-assistant", "value": "kodi-iptv-assistant"}})  # fmt: skip
    b.row("Containers")
    b.graph("Processor per container", "Share of one processor core. Plex installed outside Docker isn't here; see the computer's processor below.",
            [(f"sum by ({SVC}) (rate(container_cpu_usage_seconds_total{{{PROJECT}}}[2m]))", f"{{{{{SVC}}}}}")],
            "percentunit", stack=True, no_value=NO_CONTAINERS)  # fmt: skip
    b.graph("Memory per container", "Memory each container uses.",
            [(f"sum by ({SVC}) (container_memory_working_set_bytes{{{PROJECT}}})", f"{{{{{SVC}}}}}")], "bytes", stack=True, no_value=NO_CONTAINERS)  # fmt: skip
    b.graph("Sent per container", "What each container sends. Threadfin's line is the TV it's streaming to Plex.",
            [(f"sum by ({SVC}) (rate(container_network_transmit_bytes_total{{{PROJECT}}}[2m])) * 8", f"{{{{{SVC}}}}}")], "bps", no_value=NO_CONTAINERS)  # fmt: skip
    b.graph("Received per container", "What each container downloads: streams for Threadfin and Pluto, guides and checks for kia.",
            [(f"sum by ({SVC}) (rate(container_network_receive_bytes_total{{{PROJECT}}}[2m])) * 8", f"{{{{{SVC}}}}}")], "bps", no_value=NO_CONTAINERS)  # fmt: skip
    b.row("The computer")
    b.stat("Processor", "How busy the processor is, all cores together.",
           '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m]))', "percentunit", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.9}])  # fmt: skip
    b.stat("Memory used", "Memory in use, not counting what's free to reuse.", MEMORY_USED, "percentunit", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.8}, {"color": "red", "value": 0.95}])  # fmt: skip
    b.stat("Disk free", "Free space on the computer's main disk.",
           'node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}', "percentunit", decimals=0,
           thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.1}, {"color": "green", "value": 0.2}])  # fmt: skip
    b.stat("Load", "Programs waiting for the processor, averaged over a minute.", "node_load1", decimals=1)
    b.stat("Cores", "Processor cores (threads).", 'count(node_cpu_seconds_total{mode="idle"})')
    b.stat("Up for", "Since the computer started.", "time() - node_boot_time_seconds", "s")
    b.graph("Processor", "How busy the processor is, by what it's doing.",
            [('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[2m])) / scalar(count(node_cpu_seconds_total{mode="idle"}))', "{{mode}}")],
            "percentunit", stack=True)  # fmt: skip
    nics = 'device!~"lo|veth.*|docker.*|br-.*|cni.*|flannel.*|vxlan.*|tailscale.*"'
    b.graph("Network", "The computer's own network connections, in and out.",
            [(f"sum(rate(node_network_receive_bytes_total{{{nics}}}[2m])) * 8", "Received"),
             (f"sum(rate(node_network_transmit_bytes_total{{{nics}}}[2m])) * 8", "Sent")], "bps")  # fmt: skip
    return b


def media(name: str, title: str, url: str) -> Board:
    """The Plex or Jellyfin dashboard: who's watching, from kia's kia_<name>_* numbers."""
    off = f"kia isn't set up to ask {title} ({url} in .env)"
    b = Board(f"kia-{name}", title, f"Who's watching on {title}, from kia ({url} in .env).", off)
    sessions = f"kia_{name}_sessions"
    b.row("Now")
    b.stat(title, f"Whether {title} answered kia.", f"kia_{name}_up",
           mappings=[{"type": "value", "options": {"0": {"text": "Not answering", "color": "red"}, "1": {"text": "OK", "color": "green"}}}],
           thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}])  # fmt: skip
    b.stat("Watching Live TV", f"{title} players on Live TV now.", f'sum({sessions}{{kind="live"}}) or vector(0)', w=5)
    b.stat("Watching anything", f"{title} players playing anything now.", f'sum({sessions}{{kind!="none"}}) or vector(0)', w=5)
    b.stat("Converting video", f"Players {title} is converting video for (transcoding), which takes the most processor time.",
           f'sum({sessions}{{video="transcode"}}) or vector(0)', w=5)  # fmt: skip
    b.stat("Away from home", "Players outside your home network.", f'sum({sessions}{{location="wan"}}) or vector(0)', w=5)
    b.table("Playing now", f"Every {title} player now, what it's playing, and how. Players are shown by a pseudonym; "
            "kia's System page (the secure one) shows which is which.", f"kia_{name}_session_info",
            ["player", "product", "kind", "video", "location", "state"], no_value="Nothing playing")  # fmt: skip
    b.row("Over time")
    b.graph("Sessions by kind", "Live TV, movies, episodes, and music.", [(f'sum by (kind) ({sessions}{{kind!="none"}})', "{{kind}}")],
            stack=True, no_value="Nothing played in this time")  # fmt: skip
    b.graph("Sessions by video", "Copied as it is (copy, directplay) or converted (transcode).",
            [(f'sum by (video) ({sessions}{{kind!="none"}})', "{{video}}")], stack=True, no_value="Nothing played in this time")  # fmt: skip
    b.graph("Converting on", f"Where {title} converts video: on the graphics card (gpu) or the processor (cpu).",
            [(f'sum by (hw) ({sessions}{{video="transcode"}})', "{{hw}}")], stack=True, no_value="Nothing converted in this time")  # fmt: skip
    return b


def plex() -> Board:
    b = media("plex", "Plex", "KIA_PLEX_URL")
    b.graph("Threadfin streaming", "What the Threadfins send to Plex: Live TV from kia's channels.",
            [(f'sum by ({SVC}) (rate(container_network_transmit_bytes_total{{{SVC}=~"threadfin.*"}}[2m])) * 8', f"{{{{{SVC}}}}}")],
            "bps", w=24, no_value="No Threadfin streaming to Plex now")  # fmt: skip
    return b


def jellyfin() -> Board:
    b = media("jellyfin", "Jellyfin", "KIA_JELLYFIN_URL")
    # Jellyfin reads kia's channels itself, so what it sends players is its own container's traffic.
    b.graph("Jellyfin streaming", "What Jellyfin in the stack sends to players. A Jellyfin outside Docker isn't here.",
            [(f'sum(rate(container_network_transmit_bytes_total{{{SVC}="jellyfin"}}[2m])) * 8', "Jellyfin")],
            "bps", w=24, no_value="No Jellyfin container in this stack")  # fmt: skip
    return b


# Real filesystems: not memory, container layers, or (on a Mac) the system's own APFS volumes,
# which share the space "/" shows anyway.
REAL_FS = 'fstype!~"tmpfs|overlay|squashfs|ramfs|vfat|nsfs", mountpoint!~"/System/Volumes/.+"'


def hardware() -> Board:
    b = Board("kia-hardware", "Hardware", "The computer's graphics card, disks, temperatures, RAID, and power: from the "
              "observability-gpu and observability-disks parts (docs/stack.md) and node-exporter. A panel without its source says "
              "why (a Mac has no graphics card or temperature data for it, for example).", NO_NODE)  # fmt: skip
    hot = [{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 85}]
    bad = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
    b.row("Graphics card (observability-gpu)")
    b.stat("Temperature", "The graphics card's temperature.", "max(DCGM_FI_DEV_GPU_TEMP)", "celsius", thresholds=hot, no_value=GPU_OFF_TILE)
    b.stat("Power", "What the card draws now.", "sum(DCGM_FI_DEV_POWER_USAGE)", "watt", decimals=0, no_value=GPU_OFF_TILE)
    b.stat("Encoder", "The card's video encoder, which Plex and Jellyfin use when they convert video on the card.",
           "max(DCGM_FI_DEV_ENC_UTIL)", "percent", no_value=GPU_OFF_TILE)  # fmt: skip
    b.stat("Decoder", "The card's video decoder.", "max(DCGM_FI_DEV_DEC_UTIL)", "percent", no_value=GPU_OFF_TILE)
    servers = '__name__=~"kia_(plex|jellyfin)_sessions"'
    b.stat("Converting on the card", "Plex and Jellyfin sessions converting video on the graphics card (hardware transcoding).",
           f'sum({{{servers}, hw="gpu"}}) or vector(0)')  # fmt: skip
    b.stat("Converting on the processor", "Plex and Jellyfin sessions converting video on the processor: slower and hotter.",
           f'sum({{{servers}, hw="cpu"}}) or vector(0)')  # fmt: skip
    b.graph("Load, encoder, and decoder", "How busy the graphics card and its video encoder and decoder are.",
            [("DCGM_FI_DEV_GPU_UTIL", "Load (GPU {{gpu}})"), ("DCGM_FI_DEV_ENC_UTIL", "Encoder (GPU {{gpu}})"),
             ("DCGM_FI_DEV_DEC_UTIL", "Decoder (GPU {{gpu}})")], "percent", no_value=GPU_OFF)  # fmt: skip
    b.graph("Temperature", "The graphics card's temperature.", [("DCGM_FI_DEV_GPU_TEMP", "GPU {{gpu}}")], "celsius", w=6, no_value=GPU_OFF)
    b.graph("Memory in use", "The graphics card's memory in use.", [("DCGM_FI_DEV_FB_USED * 1048576", "GPU {{gpu}}")], "bytes", w=6,
            no_value=GPU_OFF)  # fmt: skip
    b.row("Disks (observability-disks)")
    b.stat("Disks failing their own check", "Disks whose SMART self-assessment says they're failing: replace them soon.",
           "count(smartctl_device_smart_status == 0) or count(smartctl_device) * 0", thresholds=bad, w=6, no_value=DISKS_OFF_TILE)  # fmt: skip
    b.stat("Bad or pending sectors", "Sectors disks have replaced, are waiting to replace, or couldn't read, in all. "
           "A number that grows means a disk is wearing out.",
           'sum(smartctl_device_attribute{attribute_name=~"Reallocated_Sector_Ct|Current_Pending_Sector|Offline_Uncorrectable",'
           'attribute_value_type="raw"}) or count(smartctl_device) * 0', thresholds=bad, w=6, no_value=DISKS_OFF_TILE)  # fmt: skip
    b.stat("Hottest disk", "The warmest disk now.", 'max(smartctl_device_temperature{temperature_type="current"})', "celsius",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 50}, {"color": "red", "value": 60}], w=6,
           no_value=DISKS_OFF_TILE)  # fmt: skip
    b.stat("Disks", "Disks the exporter reads. USB drives that sleep, or whose case hides SMART, aren't counted.",
           "count(smartctl_device)", w=6, no_value=DISKS_OFF_TILE)  # fmt: skip
    attribute = 'max by (device) (smartctl_device_attribute{{attribute_name="{}",attribute_value_type="raw"}})'
    b.columns("Each disk", "SMART says OK (1) or failing (0); the counts should stay at 0. NVMe disks show their wear instead "
              "of the sector counts.", "device", [
                  ("SMART OK", "max by (device) (smartctl_device_smart_status)", "short"),
                  ("Temperature", 'max by (device) (smartctl_device_temperature{temperature_type="current"})', "celsius"),
                  ("Hours on", "max by (device) (smartctl_device_power_on_seconds) / 3600", "h"),
                  ("Replaced sectors", attribute.format("Reallocated_Sector_Ct"), "short"),
                  ("Pending sectors", attribute.format("Current_Pending_Sector"), "short"),
                  ("Unreadable sectors", attribute.format("Offline_Uncorrectable"), "short"),
                  ("Cable errors", attribute.format("UDMA_CRC_Error_Count"), "short"),
                  ("NVMe wear", "max by (device) (smartctl_device_percentage_used)", "percent"),
              ], no_value=DISKS_OFF)  # fmt: skip
    b.graph("Disk temperatures", "Each disk's temperature.",
            [('smartctl_device_temperature{temperature_type="current"}', "{{device}}")], "celsius", w=24, no_value=DISKS_OFF)  # fmt: skip
    b.row("The computer (node-exporter)")
    b.stat("Failed disks in RAID", "Disks that software RAID (mdadm) marked failed.",
           'sum(node_md_disks{state="failed"}) or vector(0)', thresholds=bad, w=6)  # fmt: skip
    b.stat("RAID rebuilding", "Arrays rebuilding or checking now; slower meanwhile.",
           'sum(node_md_state{state=~"recovering|resync"}) or vector(0)',
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}], w=6)  # fmt: skip
    b.stat("Memory errors today", "Errors the memory corrected or couldn't, in the last day (ECC memory only).",
           "(sum(increase(node_edac_correctable_errors_total[1d])) + sum(increase(node_edac_uncorrectable_errors_total[1d]))) "
           "or vector(0)", thresholds=bad, w=6, decimals=0)  # fmt: skip
    b.stat("Processor slowed for heat", "Times the processor throttled itself in the last hour, usually for heat.",
           "(sum(increase(node_cpu_core_throttles_total[1h])) + sum(increase(node_cpu_package_throttles_total[1h]))) or vector(0)",
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}], w=6, decimals=0)  # fmt: skip
    b.graph("Temperatures", "The processor's and other sensors' temperatures.",
            [("node_hwmon_temp_celsius * on (chip, sensor) group_left (label) node_hwmon_sensor_label", "{{label}}"),
             ("node_hwmon_temp_celsius unless on (chip, sensor) node_hwmon_sensor_label", "{{chip}} {{sensor}}")], "celsius",
            no_value=NO_SENSORS)  # fmt: skip
    b.graph("Fans", "Fan speeds.", [("node_hwmon_fan_rpm", "{{chip}} {{sensor}}")], "rotrpm", no_value=NO_SENSORS)
    b.columns("RAID arrays", "Software RAID (mdadm) arrays: disks working, failed, and spare, and how much is in sync.",
              "device", [
                  ("Working disks", 'max by (device) (node_md_disks{state="active"})', "short"),
                  ("Failed disks", 'max by (device) (node_md_disks{state="failed"})', "short"),
                  ("Spare disks", 'max by (device) (node_md_disks{state="spare"})', "short"),
                  ("In sync", "max by (device) (node_md_blocks_synced / node_md_blocks) * 100", "percent"),
              ], w=12, no_value="No software RAID here")  # fmt: skip
    b.graph("Free space", "Free space on each of the computer's disks, as a share of its size.",
            [(f"100 * node_filesystem_avail_bytes{{{REAL_FS}}} / node_filesystem_size_bytes{{{REAL_FS}}}", "{{mountpoint}}")], "percent")
    b.row("Power")
    b.stat("Running on", "Mains power or battery, for a UPS or laptop battery node-exporter can see.",
           'max(node_power_supply_power_source_state{state="Battery Power"}) or (max(node_power_supply_online) * 0)',
           mappings=[{"type": "value", "options": {"0": {"text": "Mains", "color": "green"}, "1": {"text": "Battery", "color": "orange"}}}],
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}], w=6, no_value=NO_UPS_TILE)  # fmt: skip
    b.stat("Battery charge", "How full the UPS's or laptop's battery is.",
           "min(node_power_supply_current_capacity / node_power_supply_max_capacity) * 100 or min(node_power_supply_capacity)",
           "percent", thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 30}, {"color": "green", "value": 60}],
           w=6, decimals=0, no_value=NO_UPS_TILE)  # fmt: skip
    b.stat("Battery time left", "How long the battery would last, by its own estimate.",
           "min(node_power_supply_time_to_empty_seconds)", "s", w=6, no_value=NO_UPS_TILE)  # fmt: skip
    return b


def write() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = []
    for board in (kia(), stack(), plex(), jellyfin(), hardware()):
        path = OUT / f"{board.uid}.json"
        path.write_text(json.dumps(board.json(), indent=1) + "\n")
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write():
        print(p.relative_to(OUT.parent.parent.parent))

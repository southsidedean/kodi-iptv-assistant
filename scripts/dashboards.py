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
PROJECT = 'container_label_com_docker_compose_project="$project"'


class Board:
    def __init__(self, uid: str, title: str, description: str) -> None:
        self.uid, self.title, self.description = uid, title, description
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
             mappings: list | None = None, thresholds: list | None = None, decimals: int | None = None) -> None:  # fmt: skip
        p = self._panel("stat", title, description, [(expr, "")], w, 4, unit)
        d = p["fieldConfig"]["defaults"]
        if mappings:
            d["mappings"] = mappings
        d["thresholds"] = {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}
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

    def json(self) -> dict:
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
    b = Board("kia", "kia", "kia's refreshes, channels, guide, sources, and Pluto TV relay (docs/metrics.md).")
    b.row("Now")
    b.stat("Last refresh", "The nightly refresh: OK, failed, or running now.",
           "kia_refresh_running * 2 + (1 - kia_refresh_running) * kia_last_refresh_success", mappings=OK_FAILED,
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
    b = Board("kia-stack", "TV stack", "Each container (kia, web, Threadfin, Plex) and the computer they run on.")
    b.variables.append({"name": "project", "label": "Compose project", "type": "query", "datasource": DS,
                        "query": {"query": "label_values(container_last_seen, container_label_com_docker_compose_project)", "refId": "P"},
                        "refresh": 2, "current": {"text": "kodi-iptv-assistant", "value": "kodi-iptv-assistant"}})  # fmt: skip
    b.row("Containers")
    b.graph("Processor per container", "Share of one processor core. Plex installed outside Docker isn't here; see the computer's processor below.",
            [(f"sum by ({SVC}) (rate(container_cpu_usage_seconds_total{{{PROJECT}}}[2m]))", f"{{{{{SVC}}}}}")],
            "percentunit", stack=True)  # fmt: skip
    b.graph("Memory per container", "Memory each container uses.",
            [(f"sum by ({SVC}) (container_memory_working_set_bytes{{{PROJECT}}})", f"{{{{{SVC}}}}}")], "bytes", stack=True)  # fmt: skip
    b.graph("Sent per container", "What each container sends. Threadfin's line is the TV it's streaming to Plex.",
            [(f"sum by ({SVC}) (rate(container_network_transmit_bytes_total{{{PROJECT}}}[2m])) * 8", f"{{{{{SVC}}}}}")], "bps")  # fmt: skip
    b.graph("Received per container", "What each container downloads: streams for Threadfin and Pluto, guides and checks for kia.",
            [(f"sum by ({SVC}) (rate(container_network_receive_bytes_total{{{PROJECT}}}[2m])) * 8", f"{{{{{SVC}}}}}")], "bps")  # fmt: skip
    b.row("The computer")
    b.stat("Processor", "How busy the processor is, all cores together.",
           '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m]))', "percentunit", decimals=0,
           thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.9}])  # fmt: skip
    b.stat("Memory used", "Memory in use, not counting what's free to reuse.",
           "1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes", "percentunit", decimals=0,
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


def plex() -> Board:
    b = Board("kia-plex", "Plex", "Who's watching on Plex, from kia (KIA_PLEX_URL in .env).")
    b.row("Now")
    b.stat("Plex", "Whether Plex answered kia.", "kia_plex_up",
           mappings=[{"type": "value", "options": {"0": {"text": "Not answering", "color": "red"}, "1": {"text": "OK", "color": "green"}}}],
           thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}])  # fmt: skip
    b.stat("Watching Live TV", "Plex players on Live TV now.", 'sum(kia_plex_sessions{kind="live"}) or vector(0)', w=5)
    b.stat("Watching anything", "Plex players playing anything now.", 'sum(kia_plex_sessions{kind!="none"}) or vector(0)', w=5)
    b.stat("Converting video", "Players Plex is converting video for (transcoding), which takes the most processor time.",
           'sum(kia_plex_sessions{video="transcode"}) or vector(0)', w=5)  # fmt: skip
    b.stat("Away from home", "Players outside your home network.", 'sum(kia_plex_sessions{location="wan"}) or vector(0)', w=5)
    b.table("Playing now", "Every Plex player now, what it's playing, and how. Players are shown by a pseudonym; "
            "kia's System page (the secure one) shows which is which.", "kia_plex_session_info",
            ["player", "product", "kind", "video", "location", "state"], no_value="Nothing playing")  # fmt: skip
    b.row("Over time")
    b.graph("Sessions by kind", "Live TV, movies, episodes, and music.", [('sum by (kind) (kia_plex_sessions{kind!="none"})', "{{kind}}")],
            stack=True, no_value="Nothing played in this time")  # fmt: skip
    b.graph("Sessions by video", "Copied as it is (copy, directplay) or converted (transcode).",
            [('sum by (video) (kia_plex_sessions{kind!="none"})', "{{video}}")], stack=True, no_value="Nothing played in this time")  # fmt: skip
    b.graph("Threadfin streaming", "What the Threadfins send to Plex: Live TV from kia's channels.",
            [(f'sum by ({SVC}) (rate(container_network_transmit_bytes_total{{{SVC}=~"threadfin.*"}}[2m])) * 8', f"{{{{{SVC}}}}}")],
            "bps", w=24)  # fmt: skip
    return b


def write() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = []
    for board in (kia(), stack(), plex()):
        path = OUT / f"{board.uid}.json"
        path.write_text(json.dumps(board.json(), indent=1) + "\n")
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write():
        print(p.relative_to(OUT.parent.parent.parent))

"""tools/playback/record.py: records Plex, Roku, and a channel's source side by side while you
watch, so a hiccup can be traced to the hop that fell behind. A stand-in server plays Plex,
a Roku, and a stream."""

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "playback" / "record.py"
spec = importlib.util.spec_from_file_location("playback_record", TOOL)
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)

SESSIONS = {"MediaContainer": {"Metadata": [
    {"live": "1", "title": "Battlestar Galactica", "viewOffset": 30000,
     "Player": {"title": "Living Room", "product": "Plex for Roku", "platform": "Roku", "state": "playing"},
     "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "copy", "sourceVideoCodec": "h264",
                          "videoCodec": "h264", "transcodeHwEncoding": "nvenc", "speed": "0.8", "maxOffsetAvailable": "38"},
     "Media": [{"videoResolution": "720p"}]},
    {"title": "A movie", "Player": {"title": "Den"}},  # not Live TV: left out
    {"live": "1", "title": "News", "viewOffset": "x", "Player": {"product": "Plex Web"}, "Media": "odd"},
]}}  # fmt: skip

ACTIVITIES = {"MediaContainer": {"Activity": [
    {"type": "provider.subscription.refresh", "title": "Refreshing Sub", "subtitle": "Grabbing", "progress": 50},
    {"type": "provider.subscription.refresh", "title": "Refreshing Sub", "subtitle": "Grabbing", "progress": 0},
    {"type": "library.update.section", "title": "Scanning"},  # not a grab
    "odd",
]}}

MEDIA_PLAYER = """<?xml version="1.0" encoding="UTF-8" ?>
<player error="false" state="buffer"><plugin bandwidth="5000000 bps" id="13535" name="Plex"/>
<format audio="aac" captions="none" container="hls" drm="none" video="mpeg4_10b" video_res="1280x720"/>
<buffering current="400" max="1000" target="0"/><position>61000 ms</position><is_live>true</is_live>
<stream_segment media_sequence="20" time="75500" bitrate="2533000" segment_type="mux"/></player>"""


class Fake(BaseHTTPRequestHandler):
    limited = False
    pieces = 0
    tokens: ClassVar[list[str]] = []  # what Plex was sent, to check the token went only there

    def do_GET(self):
        Fake.tokens.append(self.headers.get("X-Plex-Token", ""))
        if self.path == "/status/sessions":
            body, kind = json.dumps(SESSIONS), "application/json"
        elif self.path == "/activities":
            body, kind = json.dumps(ACTIVITIES), "application/json"
        elif self.path == "/query/active-app":
            body, kind = '<active-app><app id="13535" type="appl" version="8.0">Plex</app></active-app>', "text/xml"
        elif self.path == "/query/device-info":
            body, kind = "<device-info><user-device-name>Roku-Den</user-device-name></device-info>", "text/xml"
        elif self.path == "/query/media-player":
            if Fake.limited:
                self.send_error(403)
                return
            body, kind = MEDIA_PLAYER, "text/xml"
        elif self.path == "/live/master.m3u8":
            body, kind = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvariant.m3u8\n", "application/vnd.apple.mpegurl"
        elif self.path == "/live/variant.m3u8":
            # A new 1-second piece at every request: a source that keeps up.
            Fake.pieces += 1
            listed = range(max(0, Fake.pieces - 3), Fake.pieces + 3)
            body = "#EXTM3U\n" + "".join(f"#EXTINF:1.0,\npiece{n}.ts\n" for n in listed)
            kind = "application/vnd.apple.mpegurl"
        else:
            self.send_error(404)
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Fake.limited, Fake.pieces, Fake.tokens = False, 0, []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_plex_sessions_keep_live_tv_and_the_numbers_that_matter():
    live = record.plex_sessions(SESSIONS["MediaContainer"])
    assert [s["title"] for s in live] == ["Battlestar Galactica", "News"]  # the movie isn't Live TV
    s = live[0]
    assert (s["player"], s["video"], s["from"], s["to"], s["hw"], s["speed"]) == ("Living Room", "transcode", "h264", "h264", "nvenc", 0.8)
    assert s["ahead"] == 8.0  # 38 s made, 30 s played
    odd = live[1]  # answers in the wrong shape count as missing, never a crash
    assert odd["video"] == "direct play" and odd["ahead"] is None and odd["resolution"] is None
    assert record.plex_sessions({"Metadata": "nonsense"}) == [] and record.plex_sessions({}) == []


def test_a_rokus_player_answer():
    r = record.roku_player(MEDIA_PLAYER)
    assert (r["state"], r["app"], r["buffer"], r["buffer_max"], r["position_s"], r["live"]) == ("buffer", "Plex", 400, 1000, 61.0, True)
    assert r["behind"] == 14.5  # the newest piece starts 75.5 s in, the player is at 61 s
    assert record.roku_player("<player/>")["state"] is None


def test_the_source_counts_only_pieces_that_arrive_while_watching():
    src = record.Source("http://unused/")
    listed = lambda first, last: "#EXTM3U\n" + "".join(f"#EXTINF:5.0,\np{n}.ts\n" for n in range(first, last))
    src.poll(listed(0, 6))  # already there when watching began
    assert src.arrivals == []
    now = time.time()
    # Then a new 5-second piece every 4 seconds: ahead of real time.
    src.arrivals = [(now - 16 + 4 * n, 5.0) for n in range(5)]
    assert src.reading(now)["ratio"] == 1.25 and src.reading(now)["pieces"] == 5
    src.arrivals = src.arrivals[:2]
    assert src.reading(now)["ratio"] is None  # two pieces: too soon to say
    src.arrivals = [(now - 200, 5.0)] * 3
    assert src.reading(now) == {"pieces": 0, "ratio": None, "errors": 0, "silent": False}  # only just begun
    src.began = now - 90
    assert src.reading(now)["silent"]  # a minute and more with nothing new: stopped
    assert "source: nothing new for a minute" in record.line_for({"t": "2026-11-04T22:00:00", "plex": [], "roku": {}, "source": src.reading(now)})


def test_a_recording_with_plex_a_roku_and_a_source(server, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(f"KIA_PLEX_URL='{server}'\nKIA_PLEX_TOKEN=sekrit-token\n")
    out = tmp_path / "rec.jsonl"
    kia = tmp_path / "kia"
    (kia / "output").mkdir(parents=True)
    (kia / "output" / "curated.m3u").write_text(f'#EXTM3U\n#EXTINF:-1 tvg-id="x",Battlestar Galactica\n{server}/live/master.m3u8\n')
    assert record.main(["--env", str(env), "--roku", server, "--watch", "battlestar galactica", "--kia", str(kia),
                        "--every", "0.3", "--minutes", str(1.2 / 60), "--out", str(out)]) == 0  # fmt: skip
    printed = capsys.readouterr().out
    readings = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(readings) >= 3
    first = readings[0]
    assert first["plex"][0]["title"] == "Battlestar Galactica" and first["roku"]["Roku-Den"]["state"] == "buffer"
    assert "Plex Living Room: Battlestar Galactica (transcode, 0.8x, 8s ahead)" in printed
    assert "Roku-Den: buffering, 14s behind live" in printed
    assert "Roku-Den: buffering in" in printed and "Plex Living Room: Battlestar Galactica: transcode" in printed
    assert first["grab_tasks"] == 2 and "Plex grab tasks open: 2" in printed
    assert "Plex grab tasks open: 2 at the start, 2 at the end, 2 at the most" in printed
    # The token goes to Plex, and nowhere in what's printed or kept; nor do stream addresses.
    assert "sekrit-token" in Fake.tokens
    assert "sekrit-token" not in printed + out.read_text() and "master.m3u8" not in out.read_text()


def test_a_roku_in_limited_mode_says_so(server, tmp_path, capsys):
    Fake.limited = True
    out = tmp_path / "rec.jsonl"
    assert record.main(["--plex", server, "--roku", server, "--every", "0.2", "--minutes", str(0.3 / 60), "--out", str(out)]) == 0
    reading = json.loads(out.read_text().splitlines()[0])
    assert reading["roku"]["Roku-Den"] == {"app": "Plex", "limited": True}
    assert "ECP is Limited" in capsys.readouterr().out


def test_an_unknown_channel_stops_with_a_reason(tmp_path, capsys):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "curated.m3u").write_text("#EXTM3U\n")
    assert record.main(["--watch", "Nope", "--kia", str(tmp_path), "--minutes", "0.01", "--out", str(tmp_path / "x")]) == 2
    assert "Nope: not in" in capsys.readouterr().err


def test_plex_out_of_reach_is_a_reading_not_a_crash(tmp_path):
    out = tmp_path / "rec.jsonl"
    started = time.time()
    assert record.main(["--plex", "http://127.0.0.1:9", "--every", "0.2", "--minutes", str(0.3 / 60), "--out", str(out)]) == 0
    assert json.loads(out.read_text().splitlines()[0])["plex_error"] and time.time() - started < 20


def test_a_channel_with_player_options_before_its_address(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "curated.m3u").write_text(
        "#EXTM3U\n#EXTINF:-1,CNN (720p)\n#EXTVLCOPT:http-user-agent=VLC/3.0.20 LibVLC/3.0.20\n"
        "#EXTVLCOPT:http-referrer=https://example.com/\nhttps://example.com/cnn.m3u8\n#EXTINF:-1,Other\nhttps://example.com/o.m3u8\n")
    assert record.channel_url(tmp_path, "cnn (720p)") == ("https://example.com/cnn.m3u8", "VLC/3.0.20 LibVLC/3.0.20")
    assert record.channel_url(tmp_path, "Other") == ("https://example.com/o.m3u8", None)
    assert record.channel_url(tmp_path, "Missing") is None
    assert record.Source("https://example.com/x", agent="VLC/3.0.20").headers == {"User-Agent": "VLC/3.0.20"}


def fake_ffprobe(tmp_path, packets):
    """A stand-in ffprobe that answers with these packets, whatever it's given."""
    tool = tmp_path / "ffprobe"
    tool.write_text("#!/bin/sh\ncat >/dev/null\ncat <<'END'\n" + json.dumps({"packets": packets}) + "\nEND\n")
    tool.chmod(0o755)
    return str(tool)


def test_a_pieces_picture_and_sound(tmp_path):
    video = [{"codec_type": "video", "pts_time": str(10 + n / 15), "duration_time": str(1 / 15)} for n in range(150)]
    whole = [{"codec_type": "audio", "pts_time": str(10.05 + n * 0.5), "duration_time": "0.5"} for n in range(20)]
    assert record.spans(b"x", fake_ffprobe(tmp_path, video + whole)) == {"video": 10.0, "audio": 10.0, "gap_ms": 50}
    half = whole[:10]  # CNN on election night: a piece with half its sound
    assert record.spans(b"x", fake_ffprobe(tmp_path, video + half)) == {"video": 10.0, "audio": 5.0, "gap_ms": 50}
    assert record.spans(b"x", fake_ffprobe(tmp_path, video))["gap_ms"] is None  # no sound at all
    assert record.spans(b"x", fake_ffprobe(tmp_path, half)) == {}  # no picture: can't say
    assert record.spans(b"x", str(tmp_path / "missing")) == {}


def test_the_source_counts_pieces_short_of_sound():
    src = record.Source("http://unused/", ffprobe="ffprobe")
    now = time.time()
    src.probed = [(now - 5, {"video": 10.0, "audio": 10.0, "gap_ms": 10}),
                  (now - 4, {"video": 10.0, "audio": 4.9, "gap_ms": 0}),
                  (now - 3, {"video": 5.0, "audio": 0.0, "gap_ms": None}),  # a piece with no sound
                  (now - 2, {}),  # ffprobe couldn't read it
                  (now - 200, {"video": 10.0, "audio": 0.0, "gap_ms": None})]  # over a minute ago  # fmt: skip
    r = src.reading(now)
    assert (r["looked"], r["unreadable"], r["short_audio"], r["no_audio"], r["widest_gap_ms"]) == (3, 1, 2, 1, 10)
    line = record.line_for({"t": "2026-11-05T09:00:00", "plex": [], "roku": {}, "source": r})
    assert "2 pieces short of sound (1 with none)" in line
    assert "Pieces: short of sound in 1 of 1 readings; at the worst minute, 2 of 3 (1 with no sound at all)" in record.summary(
        [{"t": "2026-11-05T09:00:00", "plex": [], "roku": {}, "source": r}])
    # Without ffprobe, the reading is the timing alone.
    assert "looked" not in record.Source("http://unused/").reading(now)


def test_encrypted_pieces_are_only_timed():
    src = record.Source("http://unused/", ffprobe="ffprobe")
    listed = lambda first, last: ('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n'
                                  + "".join(f"#EXTINF:5.0,\np{n}.ts\n" for n in range(first, last)))  # fmt: skip
    src.poll(listed(0, 3))
    src.poll(listed(1, 4))
    assert len(src.arrivals) == 1 and src.to_probe.empty() and src.reading()["encrypted"]


def fake_proc(root, cpu, plex_ticks, converters=0):
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(f"cpu  {' '.join(str(x) for x in cpu)}\ncpu0 1 2 3\n")
    (root / "loadavg").write_text("1.99 1.80 1.85 2/900 12345\n")
    (root / "pressure").mkdir(exist_ok=True)
    (root / "pressure" / "cpu").write_text("some avg10=0.02 avg60=0.04 avg300=0.00 total=1\n")
    (root / "pressure" / "io").write_text("some avg10=4.30 avg60=2.18 avg300=1.00 total=1\nfull avg10=1.00 avg60=0 avg300=0 total=1\n")
    procs = [("Plex Media Serv", plex_ticks), ("bash", 999)] + [("Plex Transcoder", 0)] * converters
    for pid, (name, ticks) in enumerate(procs, start=100):
        (root / str(pid)).mkdir(exist_ok=True)
        # Fields after the name: state, then 10 others, then user time and system time.
        (root / str(pid) / "stat").write_text(f"{pid} ({name}) S {' '.join(['0'] * 10)} {ticks} {ticks} 0 0\n")


def test_the_machines_load(tmp_path):
    proc = tmp_path / "proc"
    gpu = lambda: {"gpu": 5.0, "encoder": 12.0, "decoder": 3.0}
    host = record.Host(proc, gpu=gpu)
    host.cores = 4
    assert not host.available()
    # user nice system idle iowait irq softirq steal
    fake_proc(proc, [100, 0, 100, 700, 100, 0, 0, 0], plex_ticks=50)
    assert host.available()
    first = host.reading()
    assert first["cpu"] is None and first["plex_cpu"] is None  # nothing to compare with yet
    assert (first["load"], first["cpu_waiting"], first["io_waiting"], first["encoder"]) == (1.99, 0.02, 4.3, 12.0)
    # 1000 ticks later across all 4 cores: 750 of them idle, Plex used 100 (50 user + 50 system).
    fake_proc(proc, [250, 0, 200, 1400, 150, 0, 0, 0], plex_ticks=100, converters=2)
    second = host.reading()
    assert (second["cpu"], second["plex_cpu"], second["converters"]) == (25.0, 40.0, 2)  # 100 of 1000 ticks on 4 cores
    line = record.line_for({"t": "2026-11-05T09:00:00", "plex": [], "roku": {}, "host": second})
    assert "load 2.0/4, CPU 25%, Plex 40%, disks waited on 4%, GPU encoder 12%" in line
    assert "Host: CPU 25% on average, 25% at the most; Plex 40% of a core on average; disks waited on 4% at the most; GPU encoder 12% at the most" in record.summary(
        [{"t": "2026-11-05T09:00:00", "plex": [], "roku": {}, "host": first}, {"t": "2026-11-05T09:00:05", "plex": [], "roku": {}, "host": second}])


def test_a_recording_can_leave_the_machine_out(tmp_path):
    out = tmp_path / "rec.jsonl"
    assert record.main(["--plex", "http://127.0.0.1:9", "--no-host", "--every", "0.2", "--minutes", str(0.3 / 60), "--out", str(out)]) == 0
    assert "host" not in json.loads(out.read_text().splitlines()[0])


def test_plex_grab_tasks_are_counted():
    assert record.plex_grab_tasks(ACTIVITIES["MediaContainer"]) == 2
    assert record.plex_grab_tasks({}) == 0 and record.plex_grab_tasks({"Activity": "nonsense"}) == 0

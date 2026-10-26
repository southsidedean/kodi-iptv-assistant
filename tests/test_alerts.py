import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from kia import alerts, cli, dnscheck, grab
from kia.config import Config, Source, load


def config(tmp_path: Path, **alert_settings) -> Config:
    return Config(sources=[Source("s", "u")], state_dir=tmp_path, alerts=alerts.AlertConfig(**alert_settings))


@pytest.fixture
def ntfy():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            click = {"Click": self.headers["Click"]} if self.headers["Click"] else {}  # only when it's sent
            received.append(
                {"path": self.path, "body": body, **{k: self.headers[k] for k in ("Title", "Priority", "Tags")}, **click}
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/my-topic", received
    server.shutdown()


def test_notify_sends_ntfy_and_command(tmp_path: Path, ntfy):
    url, received = ntfy
    out = tmp_path / "sent.txt"
    command = f'printf "%s|%s|%s" "$KIA_ALERT_EVENT" "$KIA_ALERT_TITLE" "$KIA_ALERT_MESSAGE" > {out}'
    cfg = alerts.AlertConfig(ntfy_url=url, command=command)
    assert alerts.notify("dns_local", "kia: local DNS problem", "line1\nline2", tmp_path, cfg, print)
    assert received == [
        {
            "path": "/my-topic",
            "body": "line1\nline2",
            "Title": "kia: local DNS problem",
            "Priority": "high",
            "Tags": "warning",
        }
    ]
    assert out.read_text() == "dns_local|kia: local DNS problem|line1\nline2"
    assert "\tdns_local\tkia: local DNS problem\tline1 | line2" in (tmp_path / "alerts.log").read_text()


def test_alerts_say_which_kia_sent_them(tmp_path: Path, ntfy, monkeypatch):
    # Several kia installs, one ntfy topic: the name in the title, a tap opens this one's
    # Dashboard, and an alert command gets the name too.
    url, received = ntfy
    monkeypatch.setenv("KIA_NAME", "den")
    monkeypatch.setenv("KIA_PUBLIC_URL", "http://192.168.1.20:8780")
    out = tmp_path / "sent.txt"
    cfg = alerts.AlertConfig(ntfy_url=url, command=f'printf "%s|%s" "$KIA_NAME" "$KIA_ALERT_TITLE" > {out}')
    assert alerts.notify("run_failed", "kia: nightly run failed", "m", tmp_path, cfg, print)
    assert received[0]["Title"] == "kia (den): nightly run failed" and received[0]["Click"] == "http://192.168.1.20:8780"
    assert out.read_text() == "den|kia (den): nightly run failed"
    assert "\tkia (den): nightly run failed\t" in (tmp_path / "alerts.log").read_text()


def test_disabled_events_and_notices(tmp_path: Path, ntfy):
    url, received = ntfy
    msgs = []
    cfg = alerts.AlertConfig(ntfy_url=url, events=("run_failed",))
    assert not alerts.notify("channels_drop", "t", "m", tmp_path, cfg, msgs.append)
    assert not alerts.notify("daily_summary", "t", "m", tmp_path, cfg, msgs.append)  # off unless chosen
    assert received == [] and not (tmp_path / "alerts.log").exists()
    assert msgs == ["(alert 'channels_drop' is turned off) t"]

    cfg = alerts.AlertConfig(ntfy_url=url, events=("daily_summary",))
    assert not alerts.notify("daily_summary", "done", "1174 channels", tmp_path, cfg, msgs.append)  # sent, not an alert
    assert received[0]["Tags"] == "white_check_mark" and not (tmp_path / "alerts.log").exists()


def test_failed_delivery_is_logged_not_raised(tmp_path: Path):
    msgs = []
    cfg = alerts.AlertConfig(ntfy_url="http://127.0.0.1:9/x", command="exit 7")
    assert alerts.notify("run_failed", "t", "m", tmp_path, cfg, msgs.append)
    assert any("ntfy notification failed" in m for m in msgs)
    assert any("alert command failed (7)" in m for m in msgs)


def test_sharp_drop():
    assert alerts.sharp_drop(1000, 750, 25)
    assert not alerts.sharp_drop(1000, 751, 25)
    assert not alerts.sharp_drop(None, 0, 25) and not alerts.sharp_drop(0, 0, 25)
    assert not alerts.sharp_drop(500, 900, 25)


def test_count_drop_alerts_and_remembers(tmp_path: Path):
    cfg = config(tmp_path)
    assert not cli.check_count_drop(cfg, "channels", 1174, "channels_drop", "working channels")  # first run
    assert not cli.check_count_drop(cfg, "channels", 1100, "channels_drop", "working channels")
    assert cli.check_count_drop(cfg, "channels", 600, "channels_drop", "working channels")
    assert "working channels dropped from 1100 to 600" in (tmp_path / "alerts.log").read_text()
    assert alerts.load_state(tmp_path)["channels"] == 600


def test_site_failure_streak_alerts_once(tmp_path: Path):
    cfg = config(tmp_path, site_fail_runs=3)
    ok_file = tmp_path / "ok.xml"
    ok_file.write_text("<tv/>")

    def results(failing: bool):
        return [
            grab.GrabResult("good.tv", 0, 1.0, tmp_path / "good.log", ok_file),
            grab.GrabResult("bad.tv", 1 if failing else 0, 1.0, tmp_path / "bad.log", ok_file),
        ]

    assert [cli.track_site_failures(cfg, results(True)) for _ in range(5)] == [False, False, True, False, False]
    assert (tmp_path / "alerts.log").read_text().count("bad.tv failed") == 1
    cli.track_site_failures(cfg, results(False))  # recovers: streak resets
    assert "bad.tv" not in alerts.load_state(tmp_path)["site_failures"]


def test_local_dns_alert_only_when_backup_resolves(tmp_path: Path):
    cfg = config(tmp_path)
    gone = {"old.example": dnscheck.PublicLookup("old.example", "no-records", [])}
    assert not cli.alert_on_local_dns(gone, "check", cfg)
    blip = {"nl.example": dnscheck.PublicLookup("nl.example", "unreachable", [])}
    assert not cli.alert_on_local_dns(blip, "check", cfg)  # no internet for a moment
    assert not (tmp_path / "alerts.log").exists()
    local = {**gone, "cdn.example": dnscheck.PublicLookup("cdn.example", "resolves", ["1.2.3.4"])}
    assert cli.alert_on_local_dns(local, "check", cfg)
    assert "cdn.example" in (tmp_path / "alerts.log").read_text()
    assert "old.example" not in (tmp_path / "alerts.log").read_text()


def test_alert_settings_from_toml_and_env(tmp_path: Path, monkeypatch):
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    for name in ("KIA_ALERT_EVENTS", "KIA_ALERT_COMMAND", "KIA_NTFY_URL"):
        monkeypatch.setenv(name, "")  # compose passes unset values as empty strings
    cfg.write_text('[alerts]\nevents = ["run_failed"]\ndrop_percent = 40\n')
    a = load(cfg).alerts
    assert (a.events, a.drop_percent, a.ntfy_url, a.command) == (("run_failed",), 40, None, None)

    monkeypatch.setenv("KIA_ALERT_EVENTS", "dns_local, daily_summary")
    monkeypatch.setenv("KIA_NTFY_URL", " https://ntfy.sh/topic ")
    monkeypatch.setenv("KIA_ALERT_COMMAND", "echo hi")
    a = load(cfg).alerts
    assert (a.events, a.ntfy_url, a.command) == (("dns_local", "daily_summary"), "https://ntfy.sh/topic", "echo hi")

    monkeypatch.setenv("KIA_ALERT_EVENTS", "dns_local,typo")
    with pytest.raises(ValueError, match="typo"):
        load(cfg)


def test_run_and_report(tmp_path: Path, monkeypatch, ntfy):
    url, received = ntfy
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg_path = tmp_path / "kia.toml"
    cfg_path.write_text(
        f'[output]\nstate_dir = "state"\n[alerts]\nntfy_url = "{url}"\nevents = ["run_failed", "daily_summary"]\n'
    )
    for name in ("KIA_ALERT_EVENTS", "KIA_ALERT_COMMAND", "KIA_NTFY_URL"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(cli, "run_pipeline", lambda config, update: 2)
    assert cli.run_and_report(str(cfg_path), False) == 2
    assert received[-1]["Title"] == "kia: nightly run failed"

    def boom(config, update):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "run_pipeline", boom)
    assert cli.run_and_report(str(cfg_path), False) == 1
    assert received[-1]["Title"] == "kia: nightly run crashed" and "disk full" in received[-1]["body"]

    alerts.save_state(tmp_path / "state", {"channels": 1174, "guide_channels": 541})
    monkeypatch.setattr(cli, "run_pipeline", lambda config, update: 0)
    assert cli.run_and_report(str(cfg_path), False) == 0
    assert received[-1]["Title"] == "kia: refresh complete"
    assert received[-1]["body"].startswith("1174 working channels, 541 with guide data")


def test_test_alert_command(tmp_path: Path, monkeypatch, ntfy):
    url, received = ntfy
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg_path = tmp_path / "kia.toml"
    cfg_path.write_text('[output]\nstate_dir = "state"\n[alerts]\nevents = ["dns_local"]\n')
    for name in ("KIA_ALERT_EVENTS", "KIA_ALERT_COMMAND", "KIA_NTFY_URL"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["test-alert", "-c", str(cfg_path)]) == 1  # nothing configured

    monkeypatch.setenv("KIA_NTFY_URL", url)
    assert cli.main(["test-alert", "-c", str(cfg_path)]) == 0  # sent even though daily_summary is off
    assert received[-1]["Title"] == "kia: test notification"
    assert not (tmp_path / "state" / "alerts.log").exists()

    monkeypatch.setenv("KIA_NTFY_URL", "http://127.0.0.1:9/nope")
    assert cli.main(["test-alert", "-c", str(cfg_path)]) == 1


def test_email_alerts(tmp_path: Path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, msg):
            sent.append(("send", msg["Subject"], msg["From"], msg["To"], msg.get_content()))

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    email = alerts.EmailConfig(host="smtp.example.com", to=["me@example.com"], user="me@example.com", password="pw")
    cfg = alerts.AlertConfig(email=email, events=("run_failed",))
    assert alerts.notify("run_failed", "kia: nightly run failed", "Exit code 1.", tmp_path, cfg, lambda m: None)
    assert sent[:3] == [("connect", "smtp.example.com", 587), ("starttls",), ("login", "me@example.com", "pw")]
    assert sent[3][1:4] == ("kia: nightly run failed", "me@example.com", "me@example.com") and "Exit code 1." in sent[3][4]

    def refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(alerts.smtplib, "SMTP", refuse)
    said = []
    alerts.notify("run_failed", "x", "y", tmp_path, cfg, said.append)
    assert any("email notification failed" in m for m in said)  # never stops the run


def test_email_settings(tmp_path: Path, monkeypatch):
    (tmp_path / "sources.txt").write_text("/tmp/a.m3u\n")
    cfg = tmp_path / "kia.toml"
    cfg.write_text("")
    for name in ("KIA_SMTP_HOST", "KIA_EMAIL_TO", "KIA_SMTP_SECURITY", "KIA_SMTP_PORT", "KIA_SMTP_USER", "KIA_EMAIL_FROM"):
        monkeypatch.setenv(name, "")
    assert load(cfg).alerts.email is None
    monkeypatch.setenv("KIA_SMTP_HOST", "smtp.example.com")
    with pytest.raises(ValueError, match="KIA_EMAIL_TO"):
        load(cfg)
    monkeypatch.setenv("KIA_EMAIL_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("KIA_SMTP_SECURITY", "ssl")
    e = load(cfg).alerts.email
    assert (e.to, e.port, e.security) == (["a@example.com", "b@example.com"], 465, "ssl")
    monkeypatch.setenv("KIA_SMTP_SECURITY", "plain")
    with pytest.raises(ValueError, match="starttls"):
        load(cfg)


def test_a_playlist_that_points_at_your_network_is_an_alert_once(tmp_path: Path, monkeypatch):
    from kia.m3u import Entry
    from kia.probe import GUARD_REFUSED, ProbeResult

    said = []
    monkeypatch.setattr(alerts, "notify", lambda event, title, message, *a: said.append((event, title, message)) or True)
    cfg = config(tmp_path)
    entries = [Entry("A", "http://192.168.1.20/a", {}, source="free"), Entry("B", "https://tv.example/b", {}, source="free"),
               Entry("C", "https://tv.example/c", {}, source="other")]  # fmt: skip
    night = {"http://192.168.1.20/a": ProbeResult(ok=False, error=cli.LAN_REFUSED),
             "https://tv.example/b": ProbeResult(ok=False, error=GUARD_REFUSED),  # a redirect, say
             "https://tv.example/c": ProbeResult(ok=True)}  # fmt: skip
    assert cli.track_playlists_on_your_network(cfg, entries, night)
    assert said[0][0] == "playlist_lan" and said[0][1] == "kia: a playlist from the internet pointed at your network: free"
    assert said[0][2].startswith("free: 2 streams.") and "the word lan" in said[0][2]
    assert not cli.track_playlists_on_your_network(cfg, entries, night)  # the next night, the same: no new alert
    fine = {u: ProbeResult(ok=True) for u in night}
    assert not cli.track_playlists_on_your_network(cfg, entries, fine)  # it stopped...
    assert cli.track_playlists_on_your_network(cfg, entries, night) and len(said) == 2  # ...and started again


def test_the_shipped_kia_toml_turns_on_the_default_events():
    # kia.toml's own list wins over the code's defaults, so a new event that's on by default
    # has to be on that list too, or every install has it off (as 0.7.1's three were, at first).
    shipped = load(Path(__file__).resolve().parent.parent / "kia.toml")
    assert shipped.alerts.events == alerts.DEFAULT_EVENTS

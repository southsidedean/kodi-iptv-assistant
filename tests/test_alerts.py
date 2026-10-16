from pathlib import Path

from kia import cli, dnscheck
from kia.alerts import raise_alert
from kia.config import Config, Source


def test_raise_alert_logs_and_runs_command(tmp_path: Path):
    out = tmp_path / "sent.txt"
    msgs = []
    raise_alert("t1", "line1\nline2", tmp_path, f'printf "%s|%s" "$KIA_ALERT_TITLE" "$KIA_ALERT_MESSAGE" > {out}', msgs.append)
    assert out.read_text() == "t1|line1\nline2"
    assert "\tt1\tline1 | line2" in (tmp_path / "alerts.log").read_text()
    raise_alert("t2", "m", tmp_path, "exit 7", msgs.append)
    assert any("alert command failed (7)" in m for m in msgs)


def test_local_dns_alert_only_when_backup_resolves_or_unreachable(tmp_path: Path):
    cfg = Config(sources=[Source("s", "u")], state_dir=tmp_path)
    gone = {"old.example": dnscheck.PublicLookup("old.example", "no-records", [])}
    assert not cli.alert_on_local_dns(gone, "check", cfg)
    assert not (tmp_path / "alerts.log").exists()

    local = {**gone, "cdn.example": dnscheck.PublicLookup("cdn.example", "resolves", ["1.2.3.4"])}
    assert cli.alert_on_local_dns(local, "check", cfg)
    assert "cdn.example" in (tmp_path / "alerts.log").read_text()
    assert "old.example" not in (tmp_path / "alerts.log").read_text()

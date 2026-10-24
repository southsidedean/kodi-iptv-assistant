import os
import platform
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [ROOT / "scripts" / "install.sh", ROOT / "scripts" / "install-service.sh", ROOT / "scripts" / "nfs-media.sh"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_and_are_executable(script: Path):
    subprocess.run(["sh", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-s", "sh", str(script)], check=True)


def test_service_file_dry_run():
    out = subprocess.run([str(SCRIPTS[1]), "--dry-run"], capture_output=True, text=True, check=True).stdout
    assert "@" not in out.replace("@apple.com", "")  # every placeholder filled in
    assert str(ROOT) in out and "kia" in out
    if platform.system() == "Linux":
        assert f"PYTHONPATH={ROOT}/src" in out and "-m kia daemon --serve" in out and "KIA_EPG_RUNNER=local" in out
    else:
        ET.fromstring(out.split("\n", 2)[2])  # well-formed plist (after the XML and DOCTYPE lines)


def test_plist_template_is_valid_xml():
    text = (ROOT / "deploy" / "kia.plist").read_text().replace("@ENV@", "")
    root = ET.fromstring(text.split("\n", 2)[2])
    keys = [k.text for k in root.iter("key")]
    assert {"Label", "ProgramArguments", "EnvironmentVariables", "RunAtLoad", "KeepAlive"} <= set(keys)


def test_daemon_takes_the_run_time_from_the_environment(monkeypatch):
    from kia import cli

    seen = {}
    monkeypatch.setattr(cli, "cmd_daemon", lambda args: seen.setdefault("at", args.at) and 0)
    monkeypatch.setenv("KIA_RUN_AT", "05:30")
    cli.main(["daemon"])
    assert seen["at"] == "05:30"


def test_installer_puts_the_databases_in_volumes_on_a_mac():
    # A folder shared from the Mac into Docker's machine breaks Plex's database.
    text = (ROOT / "scripts" / "install.sh").read_text()
    mac = text[text.index('if [ "$OS" = Darwin ]; then\n        (umask 077 && sed'):]
    mac = mac[: mac.index("\n    fi\n")]
    names = dict(re.findall(r"\^(KIA_[A-Z_]+)=\.\*\|\1=([a-z-]+)\|", mac))
    assert names == {"KIA_PLEX_CONFIG": "plex-config", "KIA_JELLYFIN_CONFIG": "jellyfin-config", "KIA_GRAFANA_DATA": "grafana-data"}
    compose = (ROOT / "compose.yaml").read_text()
    declared = re.findall(r"^  ([a-z0-9-]+):$", compose.split("\nvolumes:\n", 1)[1], flags=re.M)
    assert sorted(declared) == sorted(names.values())


def test_installer_keeps_env_private():
    # .env holds the password: made with umask 077, and an older one is tightened too.
    text = (ROOT / "scripts" / "install.sh").read_text()
    made = text.index(".env.example >.env")
    assert "umask 077" in text[made - 600:made] and "\nchmod 600 .env" in text[made:]

import os
import platform
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# The first two by place: tests below use SCRIPTS[1]. Every other script follows.
SCRIPTS = [ROOT / "scripts" / "install.sh", ROOT / "scripts" / "install-service.sh",
           *sorted(p for p in (ROOT / "scripts").glob("*.sh") if p.name not in ("install.sh", "install-service.sh"))]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_and_are_executable(script: Path):
    subprocess.run(["sh", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-s", "sh", str(script)], check=True)


def test_service_file_dry_run():
    out = subprocess.run([str(SCRIPTS[1]), "--dry-run"], capture_output=True, text=True, check=True).stdout
    assert not re.search(r"@[A-Z_]+@", out)  # every placeholder filled in (a PATH can hold an @ too)
    assert str(ROOT) in out and "kia" in out
    if platform.system() == "Linux":
        assert f"PYTHONPATH={ROOT}/src" in out and "-m kia daemon --serve" in out and "KIA_EPG_RUNNER=local" in out
    else:
        ET.fromstring(out.split("\n", 2)[2])  # well-formed plist (after the XML and DOCTYPE lines)


def test_the_service_may_write_what_the_container_may_and_install_makes_it():
    # The folders compose.yaml lets kia's container write, but plex/warmup (Plex in the stack
    # is Docker only): the Linux service must allow the same ones, or a refresh fails there.
    compose = (ROOT / "compose.yaml").read_text()
    kia = compose[compose.index("\n  kia:"):compose.index("\n  web:")]
    writable = {m for m in re.findall(r"- \./([a-z/]+):/data/\1\s*$", kia, re.M)} - {"plex/warmup"}
    assert writable == {"output", "state", "work", "vendor", "profiles", "tls"}
    unit = (ROOT / "deploy" / "kia.service").read_text()
    allowed = re.search(r"^ReadWritePaths=(.*)$", unit, re.M).group(1).split()
    assert sorted(allowed) == sorted([*(f"@DIR@/{d}" for d in writable), "%h/.npm"])
    assert re.search(r"^ProtectSystem=strict$", unit, re.M) and re.search(r"^ProtectHome=read-only$", unit, re.M)
    # A folder that isn't there when the service starts can't be made later, so the script makes each.
    made = re.search(r"^mkdir -p (output .*) \"\$\(dirname", SCRIPTS[1].read_text(), re.M).group(1).split()
    assert {d.split("/")[0] for d in made} == writable
    assert 'mkdir -p "$HOME/.npm"' in SCRIPTS[1].read_text()


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
    assert names == {"KIA_PLEX_CONFIG": "plex-config", "KIA_JELLYFIN_CONFIG": "jellyfin-config", "KIA_GRAFANA_DATA": "grafana-data", "KIA_LLDAP_DATA": "lldap-data"}
    compose = (ROOT / "compose.yaml").read_text()
    volumes = compose.split("\nvolumes:\n", 1)[1].split("\nnetworks:\n", 1)[0]  # not the networks after them
    declared = re.findall(r"^  ([a-z0-9-]+):$", volumes, flags=re.M)
    assert sorted(declared) == sorted(names.values())


def test_installer_offers_every_preset_and_applies_only_a_new_env():
    text = (ROOT / "scripts" / "install.sh").read_text()
    presets = sorted(f.stem for f in (ROOT / "stacks").glob("*.env"))
    help_text = text[text.index("--preset NAME"):text.index("--main")]
    named = re.findall(r"[a-z]+(?:-[a-z]+)*", help_text.split(":", 1)[1].split("(stacks/")[0])
    assert sorted(n for n in named if n != "or") == presets
    start = text[text.index('step "Starting kia'):text.index('step "Done"')]
    choose = text[text.index('plex_next=no apply_now=no'):text.index('step "Starting kia')]
    assert 'if [ "$new_env" = yes ]; then apply_now=yes' in choose  # never --yes on an existing .env
    assert "*,plex,* | *,plex-tuner,*) plex_next=yes" in choose  # the playlist and claim code are the person's
    assert "scripts/stack.sh apply --yes" in start


def test_installer_keeps_env_private():
    # .env holds the password: made with umask 077, and an older one is tightened too.
    text = (ROOT / "scripts" / "install.sh").read_text()
    made = text.index(".env.example >.env")
    assert "umask 077" in text[made - 600:made] and "\nchmod 600 .env" in text[made:]


def test_quotes_around_a_value_in_env_dont_reach_launchd(tmp_path: Path):
    """Docker and systemd take KEY='a $b' as a $b; launchd's file gets the value the same way."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPTS[1], tmp_path / "scripts" / "install-service.sh")
    shutil.copytree(ROOT / "deploy", tmp_path / "deploy")
    (tmp_path / ".env").write_text("KIA_ALERT_COMMAND='echo \"$KIA_ALERT_TITLE\"'\nKIA_RUN_AT=\"05:30\"\nKIA_EPG_DAYS=3\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (bin_dir / "uname").chmod(0o755)
    out = subprocess.run(["sh", str(tmp_path / "scripts" / "install-service.sh"), "--dry-run"], capture_output=True,
                         text=True, check=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}).stdout  # fmt: skip
    root = ET.fromstring(out[out.index("<plist"):])
    top = list(root.find("dict"))
    found = next(top[i + 1] for i, e in enumerate(top) if e.tag == "key" and e.text == "EnvironmentVariables")
    env = {k.text: v.text for k, v in zip(list(found)[::2], list(found)[1::2], strict=True)}
    assert env["KIA_ALERT_COMMAND"] == 'echo "$KIA_ALERT_TITLE"' and env["KIA_RUN_AT"] == "05:30" and env["KIA_EPG_DAYS"] == "3"


def test_nfs_media_reads_a_dot_in_a_name_as_a_dot():
    text = (ROOT / "scripts" / "nfs-media.sh").read_text()
    literal = next(line for line in text.splitlines() if line.startswith("literal()"))
    script = literal + '\nprintf "movies-4k x\\nmovies.4k y\\n" | grep "^$(literal movies.4k)[[:space:]]"'
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout
    assert out == "movies.4k y\n"

"""scripts/stack.sh: KIA_STACK in .env becomes Compose profiles, checked for this computer,
and apply starts what's chosen and stops what isn't. Docker, uname, and nc are stand-ins."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")


@pytest.fixture
def kia(tmp_path: Path) -> Path:
    """A kia folder with the script, the presets, an .env, and stand-ins for docker, uname, and nc:
    Linux, nothing on port 9100, Docker without NVIDIA, kia and jellyfin running, nobody watching."""
    folder = tmp_path / "kia"
    (folder / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "stack.sh", folder / "scripts" / "stack.sh")
    shutil.copytree(ROOT / "stacks", folder / "stacks")
    (folder / "media").mkdir()
    (folder / "output").mkdir()
    for name in ("plex", "plex-2"):
        (folder / "output" / f"{name}.m3u").write_text("#EXTM3U\n")  # the playlists Threadfin reads
    (folder / ".env").write_text("KIA_PUBLIC_URL=http://192.168.1.20:8780\nKIA_PORT=1\nCOMPOSE_PROFILES=\n"
                                 "KIA_PLEX_ADVERTISE=http://192.168.1.20:32400/\nKIA_JELLYFIN_ADVERTISE=\n")  # fmt: skip
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    stub(bin_dir, "docker", f'echo "COMPOSE_PROFILES=$COMPOSE_PROFILES $*" >> {log}\n'
         'case "$*" in\n'
         '  info*) [ -n "$FAKE_NVIDIA" ] && echo " Runtimes: io.containerd.runc.v2 nvidia runc" ;;\n'
         '  *"ps --services --status running"*) printf "kia\\nweb\\njellyfin\\n" ;;\n'
         '  *"ps --all --services"*) printf "kia\\nweb\\njellyfin\\nthreadfin-2\\n" ;;\n'
         '  *"config --services"*) printf "kia\\nweb\\nthreadfin\\nplex\\n" ;;\n'
         '  *"ps --all --format"*) printf "kia\\trunning\\thealthy\\tUp 2 hours\\n" ;;\n'
         'esac\n')  # fmt: skip
    stub(bin_dir, "uname", 'echo "${FAKE_OS:-Linux}"\n')
    stub(bin_dir, "nc", 'exit 1\n')  # nothing listens anywhere
    stub(bin_dir, "curl", 'echo "kia_pluto_channels_playing 0"\n')  # kia's numbers: nobody watching
    return folder


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def stack(kia: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": f"{kia.parent / 'bin'}:/usr/bin:/bin", "HOME": str(kia.parent), **env}
    return subprocess.run(["sh", "scripts/stack.sh", *args], cwd=kia, env=full, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def with_stack(kia: Path, value: str) -> None:
    (kia / ".env").write_text((kia / ".env").read_text() + f"KIA_STACK={value}\n")


def test_parts_become_profiles(kia: Path):
    with_stack(kia, "plex,observability, plex-tuner-2")
    r = stack(kia, "check")
    assert r.returncode == 0, r.stdout
    assert "COMPOSE_PROFILES=plex,plex-server,monitoring,plex-2,monitoring-node" in r.stdout  # node-exporter: nothing on 9100
    assert "add observability-disks" in r.stdout and "NVIDIA card" not in r.stdout  # hints for this computer


def test_mistakes_are_problems(kia: Path):
    with_stack(kia, "plex,plex-tuner,observability-gpu,plexx")
    r = stack(kia, "check")
    assert r.returncode == 1
    for said in ("plexx, which isn't a part", "plex already brings its tuner", "needs an NVIDIA card",
                 "go with observability"):  # fmt: skip
        assert said in r.stdout, said
    assert "problem(s)" in r.stdout


def test_a_mac_gets_the_mac_rules(kia: Path):
    with_stack(kia, "jellyfin,observability,observability-disks")
    r = stack(kia, "check", FAKE_OS="Darwin")
    assert r.returncode == 1
    assert "for Linux; on a Mac" in r.stdout
    assert "brew install node_exporter" in r.stdout  # nothing answers on 9100
    assert "KIA_JELLYFIN_CONFIG keeps its database in a folder" in r.stdout
    assert "monitoring-node" not in r.stdout  # a Mac's node-exporter comes from Homebrew


def test_apply_empties_the_address_of_a_part_it_drops(kia: Path):
    # Plex leaves, Jellyfin stays: Plex's address in the stack goes, and comes back with Plex.
    def apply(*args):
        for old in (kia / "stack-service").glob("env.before-stack-*"):  # each apply's copy of .env is named by the second
            old.unlink()
        return stack(kia, "apply", *args, "--yes")

    with (kia / ".env").open("a") as f:
        f.write("KIA_JELLYFIN_TOKEN=key\n")
    assert apply("--preset", "plex-box").returncode == 0
    with_stack(kia, "jellyfin")
    r = apply()
    env = (kia / ".env").read_text()
    assert r.returncode == 0 and "Emptied KIA_PLEX_URL" in r.stdout
    assert "KIA_PLEX_URL=\n" in env and "KIA_JELLYFIN_URL=http://jellyfin:8096\n" in env
    assert "KIA_JELLYFIN_TOKEN=key\n" in env  # Jellyfin's key stays for when it's back
    with_stack(kia, "plex")
    assert apply().returncode == 0
    env = (kia / ".env").read_text()
    assert "KIA_PLEX_URL=http://plex:32400\n" in env and "KIA_JELLYFIN_URL=\n" in env
    # A Plex on another computer is yours: dropping the parts leaves its address alone.
    with_stack(kia, "plex-tuner")
    (kia / ".env").write_text((kia / ".env").read_text().replace("KIA_PLEX_URL=http://plex:32400", "KIA_PLEX_URL=http://192.168.1.20:32400"))
    r = apply()
    assert "KIA_PLEX_URL=http://192.168.1.20:32400\n" in (kia / ".env").read_text() and "Emptied" not in r.stdout


def test_nvidia_is_offered_and_allowed(kia: Path):
    with_stack(kia, "observability")
    assert "this computer has an NVIDIA card" in stack(kia, "check", FAKE_NVIDIA="1").stdout
    with_stack(kia, "observability,observability-gpu")
    assert stack(kia, "check", FAKE_NVIDIA="1").returncode == 0


def test_apply_sets_the_profiles_and_stops_what_is_no_longer_chosen(kia: Path):
    r = stack(kia, "apply", "--preset", "plex-box", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    env = (kia / ".env").read_text()
    assert "KIA_STACK=plex\n" in env and "COMPOSE_PROFILES=plex,plex-server\n" in env
    assert "KIA_PLEX_URL=http://plex:32400\n" in env  # Plex in the stack, as kia reaches it
    calls = (kia.parent / "docker.log").read_text().splitlines()
    assert any(c.endswith(" compose up -d --quiet-pull") for c in calls)
    assert any("rm --stop --force jellyfin" in c and c.startswith("COMPOSE_PROFILES=* ") for c in calls)  # not chosen any more
    assert any("rm --stop --force threadfin-2" in c for c in calls)  # stopped or stuck restarting, it goes too
    assert not any("rm --stop --force kia" in c for c in calls)
    assert list((kia / "stack-service").glob("env.before-stack-*"))  # the old .env, kept, where kia can't write
    assert not list((kia / "state").glob("env.before-stack-*"))
    assert "Next for Plex" in r.stdout


def test_apply_refuses_a_stack_with_problems(kia: Path):
    with_stack(kia, "observability-gpu")
    r = stack(kia, "apply", "--yes")
    assert r.returncode == 1
    assert not (kia.parent / "docker.log").exists() or "up -d" not in (kia.parent / "docker.log").read_text()


def test_without_kia_stack_it_says_what_compose_profiles_does(kia: Path):
    r = stack(kia, "check")
    assert r.returncode == 1 and "KIA_STACK isn't in .env" in r.stdout
    assert stack(kia, "list").returncode == 0
    assert stack(kia, "apply", "--preset", "nope").returncode == 2


def test_apply_asks_before_turning_off_what_runs_now(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=plex,monitoring\n")
    (kia / ".env").write_text(env + "KIA_STACK=jellyfin\n")
    r = stack(kia, "apply")  # no terminal to answer: nothing changes
    assert "These Compose profiles stop, as KIA_STACK leaves them out: plex, monitoring" in r.stdout
    assert "Not changed" in r.stdout and "COMPOSE_PROFILES=plex,monitoring\n" in (kia / ".env").read_text()
    assert stack(kia, "apply", "--yes").returncode == 0
    assert "COMPOSE_PROFILES=jellyfin-server\n" in (kia / ".env").read_text()


def test_threadfin_needs_its_playlist_first(kia: Path):
    (kia / "output" / "plex.m3u").unlink()
    with_stack(kia, "plex")
    r = stack(kia, "check")
    assert r.returncode == 1 and "no playlist named plex for Threadfin yet" in r.stdout
    assert "plex-box.md, step 2" in r.stdout  # Plex in the stack: the cookbook
    (kia / "output" / "plex-1.m3u").write_text("#EXTM3U\n")  # the split names work too
    with_stack(kia, "plex-tuner,plex-tuner-2")
    (kia / "output" / "plex-2.m3u").unlink()
    r = stack(kia, "check")
    assert "no playlist named plex-2 for the second Threadfin" in r.stdout and "named plex for" not in r.stdout
    (kia / "output" / "plex-1.m3u").unlink()
    assert "docs/plex.md, step 1" in stack(kia, "check").stdout  # a Plex elsewhere: the Plex guide


def test_an_empty_kia_stack_leaves_compose_profiles_set_by_hand_alone(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=plex\n")
    (kia / ".env").write_text(env + "KIA_STACK=\n")  # as .env.example has it, then docs/plex.md by hand before 0.5.6
    r = stack(kia, "check")
    assert r.returncode == 1 and "KIA_STACK is empty, so COMPOSE_PROFILES decides what runs: plex" in r.stdout
    assert stack(kia, "apply", "--yes").returncode == 1
    assert "COMPOSE_PROFILES=plex\n" in (kia / ".env").read_text()
    assert not (kia.parent / "docker.log").exists() or "up -d" not in (kia.parent / "docker.log").read_text()
    assert stack(kia, "apply", "--preset", "plex-elsewhere", "--yes").returncode == 0  # a preset is a choice


def test_apply_starts_its_profiles_even_with_others_in_the_shell(kia: Path):
    r = stack(kia, "apply", "--preset", "plex-box", "--yes", COMPOSE_PROFILES="monitoring")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (kia.parent / "docker.log").read_text().splitlines()
    assert "COMPOSE_PROFILES=plex,plex-server compose up -d --quiet-pull" in calls


def request(kia: Path, **fields: str) -> Path:
    """A request from the Dashboard's Stack page, as kia leaves it in state/."""
    (kia / "state").mkdir(exist_ok=True)
    path = kia / "state" / "stack-request"
    path.write_text("".join(f"{k}={v}\n" for k, v in fields.items()))
    return path


def answer(kia: Path) -> tuple[dict, str]:
    head, _, said = (kia / "stack-service" / "result").read_text().partition("\n\n")
    return dict(line.split("=", 1) for line in head.splitlines()), said


def test_check_can_try_a_choice_without_changing_env(kia: Path):
    with_stack(kia, "plex")
    before = (kia / ".env").read_text()
    r = stack(kia, "check", "--stack", "jellyfin")
    assert r.returncode == 0 and "COMPOSE_PROFILES=jellyfin-server" in r.stdout
    assert (kia / ".env").read_text() == before


def test_stack_takes_part_names_only(kia: Path):
    # Whatever reaches --stack goes into .env through awk, which reads backslashes as escapes.
    for odd in ("plex;reboot", "plex\\n", "plex$(id)", "PLEX"):
        r = stack(kia, "check", "--stack", odd)
        assert r.returncode == 2 and "part names" in r.stderr, odd


def test_watch_carries_out_a_check_and_lists_the_containers(kia: Path):
    with_stack(kia, "plex")
    before = (kia / ".env").read_text()
    request(kia, id="req-1", action="check", stack="jellyfin")
    r = stack(kia, "watch", "--once")
    assert r.returncode == 0, r.stderr
    head, said = answer(kia)
    assert head["id"] == "req-1" and head["action"] == "check" and head["state"] == "finished" and head["exit"] == "0"
    assert "COMPOSE_PROFILES=jellyfin-server" in said
    assert (kia / ".env").read_text() == before and not (kia / "state" / "stack-request").exists()
    assert (kia / "stack-service" / "containers.tsv").read_text() == "kia\trunning\thealthy\tUp 2 hours\n"
    assert not (kia / "stack-service" / "watch.pid").exists()  # removed when it stops


def test_watch_applies_a_new_choice(kia: Path):
    with_stack(kia, "plex")
    request(kia, id="req-2", action="apply", stack="jellyfin")
    stack(kia, "watch", "--once")
    head, _ = answer(kia)
    assert head["state"] == "finished" and head["exit"] == "0"
    assert "KIA_STACK=jellyfin" in (kia / ".env").read_text()
    assert "COMPOSE_PROFILES=jellyfin-server compose up -d" in (kia.parent / "docker.log").read_text()


@pytest.mark.parametrize(("fields", "why"), [
    ({"action": "rm"}, "other than check or apply"),
    ({"stack": "plex$(touch hacked)"}, "only be names"),
    ({"stack": "plex\\nobservability"}, "only be names"),
    ({"stack": "plex," * 60}, "too long"),
])  # fmt: skip
def test_watch_refuses_a_request_it_cant_trust(kia: Path, fields, why):
    with_stack(kia, "plex")
    before = (kia / ".env").read_text()
    request(kia, **{"id": "req-3", "action": "apply", "stack": "jellyfin", **fields})
    stack(kia, "watch", "--once")
    head, said = answer(kia)
    assert head["state"] == "refused" and head["exit"] == "2" and why in said
    assert (kia / ".env").read_text() == before and "up -d" not in (kia.parent / "docker.log").read_text()
    assert not list(kia.rglob("hacked"))


def test_watch_refuses_an_old_request(kia: Path):
    import os
    import time

    path = request(kia, id="req-4", action="apply", stack="jellyfin")
    old = time.time() - 20 * 60
    os.utime(path, (old, old))
    stack(kia, "watch", "--once")
    head, said = answer(kia)
    assert head["state"] == "refused" and "5 minutes old" in said


def test_watch_ignores_a_request_without_a_proper_id(kia: Path):
    request(kia, id="../../etc", action="check", stack="plex")
    r = stack(kia, "watch", "--once")
    assert "without a proper id" in r.stdout and not (kia / "stack-service" / "result").exists()
    assert not (kia / "state" / "stack-request").exists() and not (kia / "stack-service" / "request").exists()


def test_only_one_watch_at_a_time(kia: Path):
    import os

    (kia / "stack-service").mkdir()
    pidfile = kia / "stack-service" / "watch.pid"
    # A process that looks like a running watch, by its command line.
    other = subprocess.Popen(["sh", "-c", "sleep 30; :", "x", "scripts/stack.sh", "watch"])
    try:
        pidfile.write_text(f"{other.pid}\n")
        r = stack(kia, "watch", "--once")
        assert r.returncode == 1 and "already running" in r.stderr
    finally:
        other.kill()
        other.wait()
    # Anything else in the file doesn't stop it: a process that isn't a watch (this test's),
    # or what a broken kia might write (kill -0 0 and kill -0 -1 would say "running").
    for stale in (str(os.getpid()), "0", "-1", "", "abc"):
        pidfile.write_text(stale + "\n")
        assert stack(kia, "watch", "--once").returncode == 0, stale


# --- What a taken-over kia container could leave in state/ --------------------------
# The kia container can write anything in state/. The service must never write, read, or
# follow anything there apart from renaming the request away.

def victim(kia: Path) -> Path:
    """A file outside the kia folder that nothing should touch."""
    path = kia.parent / "victim"
    path.write_text("keep me\n")
    return path


def test_the_service_writes_nothing_in_state(kia: Path):
    # state/ is the kia container's: the service only takes the request out of it, renaming it
    # into stack-service/. Links planted under any name there are never written through.
    target = victim(kia)
    with_stack(kia, "plex")
    request(kia, id="req-6", action="check", stack="jellyfin")
    for name in ("stack-result", "stack-containers.tsv", "result", "containers.tsv", "kia-stack.log"):
        (kia / "state" / name).symlink_to(target)
    before = sorted(p.name for p in (kia / "state").iterdir())
    assert stack(kia, "watch", "--once").returncode == 0
    assert target.read_text() == "keep me\n" and answer(kia)[0]["state"] == "finished"
    assert sorted(p.name for p in (kia / "state").iterdir()) == [n for n in before if n != "stack-request"]


def test_a_request_that_is_a_link_is_removed_unread(kia: Path):
    secret = kia.parent / "secret"
    secret.write_text("id=req-8\naction=check\nstack=plex\n")  # reads like a request, but isn't kia's
    (kia / "state").mkdir()
    (kia / "state" / "stack-request").symlink_to(secret)
    r = stack(kia, "watch", "--once")
    assert r.returncode == 0 and "wasn't a request" in r.stdout
    assert secret.read_text().startswith("id=req-8") and not (kia / "stack-service" / "result").exists()
    assert not (kia / "state" / "stack-request").is_symlink()


def test_a_request_that_is_a_folder_is_removed(kia: Path):
    (kia / "state" / "stack-request").mkdir(parents=True)
    r = stack(kia, "watch", "--once")
    assert r.returncode == 0 and not (kia / "state" / "stack-request").exists()


def test_only_the_start_of_a_huge_request_is_read(kia: Path):
    (kia / "state").mkdir()
    (kia / "state" / "stack-request").write_text("x" * 5000 + "\nid=req-9\naction=check\nstack=plex\n")
    r = stack(kia, "watch", "--once")
    assert "without a proper id" in r.stdout


def test_a_refused_apply_leaves_env_alone(kia: Path):
    with_stack(kia, "plex")
    before = (kia / ".env").read_text()
    for args in (["apply", "--stack", "plex,plex-tuner"], ["apply", "--preset", "plex-box", "--stack", "plex"]):
        assert stack(kia, *args).returncode != 0
        assert (kia / ".env").read_text() == before, args
    assert not list(kia.glob("*/env.before-stack-*"))


def test_the_env_copy_is_never_written_through_a_link(kia: Path):
    import time

    # The copy goes in stack-service/, which kia's container can't write; even so, nothing
    # already at the name is written through: a file, or a link to one or to a device
    # (set -C alone writes through a link to /dev/null or a FIFO).
    target = victim(kia)
    with_stack(kia, "plex")
    (kia / "stack-service").mkdir()
    now = time.time()
    for link_to in (target, Path("/dev/null")):
        for old in (kia / "stack-service").glob("env.before-stack-*"):
            old.unlink()
        for second in range(-1, 6):  # the copy's name has the time in it: cover the next few seconds
            name = time.strftime("env.before-stack-%Y%m%d-%H%M%S", time.localtime(now + second))
            (kia / "stack-service" / name).symlink_to(link_to)
        r = stack(kia, "apply", "--stack", "jellyfin", "--yes")
        assert target.read_text() == "keep me\n"
        assert r.returncode == 1 and "couldn't save a copy of .env" in r.stderr, link_to
        assert "KIA_STACK=plex\n" in (kia / ".env").read_text()  # and nothing changed


def test_the_stack_service_file(tmp_path: Path):
    out = subprocess.run(["sh", str(ROOT / "scripts" / "install-service.sh"), "--stack", "--dry-run"],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    assert "stack.sh" in out and "watch" in out and str(ROOT) in out
    assert "@DIR@" not in out and "@PATH@" not in out and "kia daemon" not in out


def test_the_stack_services_files_are_out_of_kias_reach():
    # On a Mac, launchd writes the service's log; a link kia planted in a folder it can write
    # (work/, state/) would send it into one of your files. Its own folder kia can only read.
    plist = (ROOT / "deploy" / "kia-stack.plist").read_text()
    paths = [p for p in plist.split("<string>") if p.startswith("@DIR@/")]
    assert paths and all(p.startswith("@DIR@/stack-service/") or p.startswith("@DIR@/scripts/") for p in paths), paths


def test_check_says_what_a_choice_would_stop(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=plex,plex-server,plex-2\n")
    (kia / ".env").write_text(env + "KIA_STACK=plex,plex-tuner-2\n")
    r = stack(kia, "check", "--stack", "plex")
    assert "Stops what runs now: plex-2" in r.stdout
    assert "Stops what runs now" not in stack(kia, "check").stdout  # the same choice stops nothing


def test_kia_asks_and_the_service_answers(kia: Path):
    # The whole path: kia leaves a request (kia.stack.ask, as the Stack page does), the real
    # scripts/stack.sh watch carries it out, and kia reads the answer the page shows.
    from kia import stack as kia_stack

    with_stack(kia, "plex")
    (kia / "state").mkdir()
    rid = kia_stack.ask(kia / "state", kia / "stack-service", "check", kia_stack.parts_from(["jellyfin"]))
    stack(kia, "watch", "--once")
    answer = kia_stack.result(kia / "stack-service")
    assert answer["id"] == rid and answer["state"] == "finished" and answer["exit"] == "0"
    assert "COMPOSE_PROFILES=jellyfin-server" in answer["output"] and kia_stack.waiting(kia / "state", kia / "stack-service") is None
    assert kia_stack.containers(kia / "stack-service")[0]["service"] == "kia"


@pytest.mark.parametrize("name", ["has space", "100%", "a&b", "x<y"])
def test_a_kia_folder_a_service_file_cant_hold_is_refused(tmp_path: Path, name: str):
    folder = tmp_path / name
    (folder / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "deploy", folder / "deploy")
    shutil.copy(ROOT / "scripts" / "install-service.sh", folder / "scripts")
    r = subprocess.run(["sh", str(folder / "scripts" / "install-service.sh"), "--stack", "--dry-run"],
                       capture_output=True, text=True, check=False)  # fmt: skip
    assert r.returncode == 1 and "can't hold" in r.stderr and "<plist" not in r.stdout


def test_apply_fills_in_the_advertise_addresses_from_kias_address(kia: Path):
    with_stack(kia, "plex")
    r = stack(kia, "check", "--stack", "plex,jellyfin")
    assert "apply sets it from KIA_PUBLIC_URL" in r.stdout and "warning: KIA_JELLYFIN_ADVERTISE" not in r.stdout
    assert stack(kia, "apply", "--stack", "plex,jellyfin", "--yes").returncode == 0
    env = (kia / ".env").read_text()
    assert "KIA_PLEX_ADVERTISE=http://192.168.1.20:32400/\n" in env  # the fixture's own stays
    assert "KIA_JELLYFIN_ADVERTISE=http://192.168.1.20:8096\n" in env


@pytest.mark.parametrize(("url", "expected"), [
    ("http://my-server.lan:8780/", "http://my-server.lan:8096"),
    ("https://[fd00::20]:8780", "http://[fd00::20]:8096"),
    ("http://localhost:8780", ""),  # other devices can't use that
    ("http://bad$(id).lan:8780", ""),
])  # fmt: skip
def test_the_address_taken_from_kias_is_checked(kia: Path, url: str, expected: str):
    env = (kia / ".env").read_text().replace("KIA_PUBLIC_URL=http://192.168.1.20:8780", f"KIA_PUBLIC_URL={url}")
    (kia / ".env").write_text(env + "KIA_STACK=jellyfin\n")
    assert stack(kia, "apply", "--yes").returncode == 0
    assert f"KIA_JELLYFIN_ADVERTISE={expected}\n" in (kia / ".env").read_text()


def test_a_claim_code_goes_into_env_with_apply(kia: Path):
    with_stack(kia, "jellyfin")
    r = stack(kia, "apply", "--stack", "plex", "--claim", "claim-Ab_1-z", "--yes")
    assert r.returncode == 0 and "claim-Ab_1-z" not in r.stdout + r.stderr  # never printed
    assert "PLEX_CLAIM=claim-Ab_1-z\n" in (kia / ".env").read_text()
    for bad in ("claim-a\\nb", "claim-$(id)", "nope", "claim-"):
        assert stack(kia, "apply", "--claim", bad, "--yes").returncode == 2, bad
    assert stack(kia, "check", "--claim", "claim-abc").returncode == 2


def test_the_service_passes_a_claim_code_on_and_refuses_a_bad_one(kia: Path):
    with_stack(kia, "jellyfin")
    request(kia, id="req-10", action="apply", stack="plex", claim="claim-good_1")
    stack(kia, "watch", "--once")
    assert answer(kia)[0]["state"] == "finished" and "PLEX_CLAIM=claim-good_1\n" in (kia / ".env").read_text()
    # No copy of the code is left behind in the service's folder.
    assert not any("claim-good_1" in f.read_text() for f in (kia / "stack-service").iterdir() if f.is_file())
    request(kia, id="req-11", action="apply", stack="plex", claim="claim-$(id)")
    stack(kia, "watch", "--once")
    head, said = answer(kia)
    assert head["state"] == "refused" and "claim code" in said
    request(kia, id="req-12", action="check", stack="plex", claim="claim-abc")
    stack(kia, "watch", "--once")
    assert answer(kia)[0]["state"] == "refused"


def test_kia_running_without_an_answer_isnt_taken_as_nobody_watching(kia: Path):
    stub(kia.parent / "bin", "curl", "exit 7\n")  # kia runs (docker says so) but its numbers don't answer
    (kia / ".env").write_text((kia / ".env").read_text() + "KIA_STACK=jellyfin\n")
    r = stack(kia, "apply")  # no terminal to answer
    assert "can't tell who's watching" in r.stdout and "Not changed" in r.stdout
    assert "--yes" in r.stdout


def test_a_port_something_else_uses_is_a_problem(kia: Path):
    stub(kia.parent / "bin", "nc", 'case "$*" in *" 32400") exit 0 ;; esac\nexit 1\n')  # another program on Plex's port
    r = stack(kia, "check", "--stack", "plex")
    assert r.returncode == 1 and "port 32400, for plex, is in use; set KIA_PLEX_PORT to another one" in r.stdout
    assert stack(kia, "check", "--stack", "jellyfin").returncode == 0  # its port is free


def test_little_memory_and_two_heavy_parts_is_a_warning(kia: Path):
    stub(kia.parent / "bin", "sysctl", "echo 4294967296\n")  # 4 GB
    r = stack(kia, "check", "--stack", "jellyfin,observability", FAKE_OS="Darwin")
    assert "this computer has 4 GB of memory; with 2 of plex, jellyfin, and observability" in r.stdout
    assert "of memory" not in stack(kia, "check", "--stack", "jellyfin", FAKE_OS="Darwin").stdout  # one is fine


def test_the_list_says_what_each_preset_asks_of_you(kia: Path):
    out = stack(kia, "list").stdout
    assert "Asks you for: A playlist named plex, made on the Dashboard; a claim code from plex.tv/claim" in out
    kodi = next(line for line in out.splitlines() if line.strip().startswith("kodi "))
    assert "Asks you for" not in out.split(kodi, 1)[1].splitlines()[1]  # nothing more for kodi


def test_a_star_in_compose_profiles_isnt_this_folders_files(kia: Path):
    env = (kia / ".env").read_text().replace("COMPOSE_PROFILES=\n", "COMPOSE_PROFILES=*\n")
    (kia / ".env").write_text(env + "KIA_STACK=jellyfin\n")
    r = stack(kia, "check")
    assert "scripts" not in r.stdout and "Stops what runs now: *" in r.stdout


def test_list_shows_the_presets_in_their_order(kia: Path):
    presets = [line.split()[0] for line in stack(kia, "list").stdout.split("Presets", 1)[1].splitlines()[1:] if line.startswith("  ") and line.split()]
    assert presets[0] == "kodi"


def test_the_gateway_part_writes_its_settings_before_it_starts(kia: Path):
    shutil.copy(ROOT / "scripts" / "gateway.sh", kia / "scripts" / "gateway.sh")
    with_stack(kia, "gateway")
    r = stack(kia, "check")
    assert r.returncode == 0 and "COMPOSE_PROFILES=gateway" in r.stdout
    assert "no device has a gateway key yet: scripts/gateway.sh add laptop" in r.stdout
    r = stack(kia, "apply", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    config = (kia / "gateway" / "config.yaml").read_text()
    assert "keys: []" in config and (kia / "gateway" / "kia-token").stat().st_size > 40
    # Written before Compose starts the gateway, which reads it only from that file.
    calls = (kia.parent / "docker.log").read_text().splitlines()
    assert any("up -d" in c and "COMPOSE_PROFILES=gateway " in c for c in calls)
    (kia / ".env").write_text((kia / ".env").read_text() + "KIA_GATEWAY_RATE=fast\n")
    r = stack(kia, "check")
    assert r.returncode == 1 and "KIA_GATEWAY_RATE" in r.stdout


def test_watch_restarts_the_gateway_when_kias_certificate_changes(kia: Path):
    # With Docker Desktop the gateway misses a renewed certificate, so the kia-stack service
    # checks every 30 seconds (scripts/gateway.sh refresh), but only with the gateway part.
    shutil.copy(ROOT / "scripts" / "gateway.sh", kia / "scripts" / "gateway.sh")
    stack(kia, "watch", "--once")
    assert "gateway" not in (kia.parent / "docker.log").read_text()
    with_stack(kia, "gateway")
    assert stack(kia, "apply", "--yes").returncode == 0
    (kia / "tls").mkdir()
    (kia / "tls" / "fullchain.pem").write_text("RENEWED\n")
    asked = (kia.parent / "docker.log").read_text().count("ps -q --status running gateway")
    stack(kia, "watch", "--once")
    assert (kia.parent / "docker.log").read_text().count("ps -q --status running gateway") == asked + 1

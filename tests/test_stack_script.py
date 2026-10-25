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
    Linux, nothing on port 9100, Docker without NVIDIA, kia and jellyfin running."""
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
    assert list((kia / "state").glob("env.before-stack-*"))  # the old .env, kept
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


@pytest.mark.parametrize("name", ["stack-result.tmp", "stack-result", "stack-containers.tmp", "stack-containers.tsv",
                                  "stack-watch.out", "stack-watch.pid", "stack-request.taken"])  # fmt: skip
def test_links_in_state_are_never_written_through(kia: Path, name: str):
    target = victim(kia)
    with_stack(kia, "plex")
    request(kia, id="req-6", action="check", stack="jellyfin")
    (kia / "state" / name).symlink_to(target)
    assert stack(kia, "watch", "--once").returncode == 0
    assert target.read_text() == "keep me\n" and answer(kia)[0]["state"] == "finished"


def test_a_link_into_a_folder_gets_nothing_moved_into_it(kia: Path):
    folder = kia.parent / "profile.d"
    folder.mkdir()
    request(kia, id="req-7", action="check", stack="plex")
    (kia / "state" / "stack-request.taken").symlink_to(folder)
    stack(kia, "watch", "--once")
    assert list(folder.iterdir()) == []


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
    assert not list((kia / "state").glob("env.before-stack-*")) if (kia / "state").exists() else True


def test_the_env_copy_is_never_written_through_a_link(kia: Path):
    import time

    target = victim(kia)
    with_stack(kia, "plex")
    (kia / "state").mkdir()
    now = time.time()
    for second in range(-1, 6):  # the copy's name has the time in it: cover the next few seconds
        name = time.strftime("env.before-stack-%Y%m%d-%H%M%S", time.localtime(now + second))
        (kia / "state" / name).symlink_to(target)
    r = stack(kia, "apply", "--stack", "jellyfin", "--yes")
    assert target.read_text() == "keep me\n"
    assert r.returncode == 1 and "couldn't save a copy of .env" in r.stderr
    assert "KIA_STACK=plex\n" in (kia / ".env").read_text()  # and nothing changed


def test_the_stack_service_file(tmp_path: Path):
    out = subprocess.run(["sh", str(ROOT / "scripts" / "install-service.sh"), "--stack", "--dry-run"],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    assert "stack.sh" in out and "watch" in out and str(ROOT) in out
    assert "@DIR@" not in out and "@PATH@" not in out and "kia daemon" not in out


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
    request(kia, id="req-11", action="apply", stack="plex", claim="claim-$(id)")
    stack(kia, "watch", "--once")
    head, said = answer(kia)
    assert head["state"] == "refused" and "claim code" in said
    request(kia, id="req-12", action="check", stack="plex", claim="claim-abc")
    stack(kia, "watch", "--once")
    assert answer(kia)[0]["state"] == "refused"

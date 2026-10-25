"""Keep the docs and the deploy files in step with each other."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "docs" / "cookbooks").glob("*.md"))]  # fmt: skip
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
# Images in HTML (the diagrams' <picture> tags).
HTML_SRC_RE = re.compile(r'(?:src|srcset)="([^"]+)"')


def test_relative_links_and_images_exist():
    broken = []
    for doc in DOCS:
        text = doc.read_text()
        for target in LINK_RE.findall(text) + HTML_SRC_RE.findall(text):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue  # web links and same-page anchors
            path = target.split("#", 1)[0]
            if not (doc.parent / path).exists():
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not broken, "broken links:\n" + "\n".join(broken)


def env_example_settings() -> list[str]:
    text = (ROOT / ".env.example").read_text()
    return re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.M)


def test_env_settings_reach_docker_and_docs():
    compose = (ROOT / "compose.yaml").read_text()
    config_doc = (ROOT / "docs" / "configuration.md").read_text()
    settings = env_example_settings()
    assert "KIA_PUBLIC_URL" in settings and len(settings) > 10
    missing_compose = [s for s in settings if "${" + s not in compose and not s.startswith("COMPOSE_")]  # COMPOSE_*: read by Docker Compose itself
    missing_docs = [s for s in settings if f"`{s}`" not in config_doc]
    assert not missing_compose, f"not used in compose.yaml: {missing_compose}"
    assert not missing_docs, f"not documented in docs/configuration.md: {missing_docs}"


def test_env_example_has_no_inline_comments():
    # Docker Compose reads "KEY=   # note" as the value "# note".
    bad = [line for line in (ROOT / ".env.example").read_text().splitlines() if re.match(r"^[A-Z][A-Z0-9_]*=.*#", line)]  # KIA_THREADFIN_2_PORT too
    assert not bad, bad


def compose_service(name: str) -> str:
    text = (ROOT / "compose.yaml").read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, flags=re.M | re.S)
    assert match, name
    return match.group(1)


def test_every_container_limits_its_log():
    section = (ROOT / "compose.yaml").read_text().split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    services = re.findall(r"^  ([a-z0-9-]+):\n", section, flags=re.M)
    assert len(services) > 5
    unlimited = [name for name in services if "logging: *logging" not in compose_service(name)]
    assert not unlimited, f"no log limit: {unlimited}"


def test_cadvisor_sees_containers_in_containerd():
    # A new Docker install keeps images in containerd; cAdvisor finds its containers only
    # through containerd's socket, and then labels them with everything, so Prometheus keeps
    # just the two labels the dashboards use.
    cadvisor = compose_service("cadvisor")
    assert "/run/containerd:/run/containerd:ro" in cadvisor and "--containerd-namespace=moby" in cadvisor
    prom = (ROOT / "deploy" / "monitoring" / "prometheus.yml").read_text()
    job = prom[prom.index("job_name: cadvisor"):prom.index("job_name: node")]
    assert "labeldrop" in job and "container_label_com_docker_compose_${1}" in job


def test_grafana_never_updates_its_own_plugins():
    # Updating a bundled plugin means deleting it from Grafana's read-only system; Grafana 13
    # drops its Prometheus plugin first, and every dashboard goes empty.
    grafana = compose_service("grafana")
    assert "read_only: true" in grafana
    assert 'GF_PLUGINS_PREINSTALL_DISABLED: "true"' in grafana and 'GF_PLUGINS_PREINSTALL_AUTO_UPDATE: "false"' in grafana


def test_containers_stay_locked_down():
    for name in ("kia", "web"):
        service = compose_service(name)
        assert "read_only: true" in service and "- ALL" in service and "no-new-privileges:true" in service, name
    kia = compose_service("kia")
    assert "init: true" in kia  # cleans up the stream tests a stopped "Check now" leaves behind
    assert "- ./:/data:ro" in kia  # kia's own code and settings can't be changed from inside
    web_mounts = re.findall(r"^\s+- (\./\S+)", compose_service("web"), flags=re.M)
    # The web server reads only the results, its own settings, and the certificate.
    assert all(m.startswith(("./output:", "./deploy/", "./tls:")) and m.endswith(":ro") for m in web_mounts), web_mounts
    for folder in ("output", "state", "work", "vendor", "profiles", "tls"):
        assert f"- ./{folder}:/data/{folder}" in kia
        assert (ROOT / folder / ".gitkeep").exists(), f"{folder}/ must exist so Docker doesn't create it as root"
    # Plex's token, wherever KIA_PLEX_CONFIG puts Plex's settings (a folder, or a volume on a Mac).
    assert "- ${KIA_PLEX_CONFIG:-./plex/config}:/data/plex/config:ro" in kia
    assert (ROOT / "plex" / "config" / ".gitkeep").exists()
    # Jellyfin's users and Grafana's password stay out of view of kia and threadfin-setup,
    # which both see the kia folder.
    for name in ("kia", "threadfin-setup"):
        service = compose_service(name)
        assert "- ./:/data:ro" in service
        for hidden in ("/data/jellyfin", "/data/monitoring"):
            assert f"- {hidden}:size=1m,mode=0500" in service, (name, hidden)


def github_anchor(heading: str) -> str:
    """How GitHub turns a heading into a #link: lowercase, punctuation dropped, spaces to -."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_links_to_sections_exist():
    broken = []
    for doc in DOCS:
        for target in LINK_RE.findall(doc.read_text()):
            if re.match(r"[a-z]+:", target) or "#" not in target:
                continue
            path, anchor = target.split("#", 1)
            page = (doc.parent / path) if path else doc
            headings = re.findall(r"^#+ (.+)$", page.read_text(), flags=re.M)
            if anchor not in {github_anchor(h) for h in headings}:
                broken.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not broken, "links to missing sections:\n" + "\n".join(broken)


def test_env_example_leaves_settings_to_their_defaults():
    """A new install copies .env.example; only the few it must set have values."""
    allowed = {"KIA_PUBLIC_URL", "KIA_BIND", "KIA_PORT", "KIA_HTTPS_PORT", "KIA_RUN_AT", "TZ", "PUID", "PGID"}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            assert not value or name in allowed, f".env.example sets {name}={value}"


def test_every_folder_compose_mounts_is_in_the_repository():
    # Docker creates a missing folder as root, and kia and Threadfin, which run as your
    # user, then can't write to it. So each one ships in the repository with a .gitkeep.
    import re
    import subprocess

    root = Path(__file__).resolve().parent.parent
    if not (root / ".git").exists():
        pytest.skip("needs the git copy (a downloaded archive has no list of tracked files)")
    tracked = set(subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split())
    # ./folder:..., and a default in a setting, ${KIA_PLEX_CONFIG:-./plex/config}:...
    mounts = re.findall(r"^\s+- (?:\./|\$\{[A-Z0-9_]+:-\./)([^:\s}]*)\}?:", (root / "compose.yaml").read_text(), re.M)
    folders = [m.rstrip("/") for m in mounts if m and "." not in Path(m).name]
    assert "threadfin/conf" in folders and "state" in folders
    missing = [f for f in folders if not any(t.startswith(f"{f}/") for t in tracked)]  # a .gitkeep, or real files
    assert not missing, missing


def test_diagrams_are_images_made_from_their_current_source():
    # docs/diagrams/<name>.mmd -> images/diagrams/<name>-light.svg and -dark.svg, made by
    # scripts/diagrams.sh, which records each source's SHA-256 in its images.
    import hashlib

    sources = sorted((ROOT / "docs" / "diagrams").glob("*.mmd"))
    assert sources
    used = "".join(doc.read_text() for doc in DOCS)
    stale, unused = [], []
    for src in sources:
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        for theme in ("light", "dark"):
            image = ROOT / "images" / "diagrams" / f"{src.stem}-{theme}.svg"
            if not image.exists() or f"source sha256 {digest}" not in image.read_text():
                stale.append(image.name)
            if f"images/diagrams/{image.name}" not in used:
                unused.append(image.name)
    assert not stale, f"run scripts/diagrams.sh, these don't match their source: {stale}"
    assert not unused, f"not used by any doc: {unused}"
    made = {f"{src.stem}-{t}.svg" for src in sources for t in ("light", "dark")}
    extra = sorted(p.name for p in (ROOT / "images" / "diagrams").glob("*.svg") if p.name not in made)
    assert not extra, f"images with no source in docs/diagrams: {extra}"


def test_docs_use_diagram_images_not_mermaid():
    # GitHub puts pan and zoom buttons on Mermaid blocks; the docs show images instead.
    assert not [doc.name for doc in DOCS if "```mermaid" in doc.read_text()]


def test_grafana_dashboards_match_their_script(tmp_path, monkeypatch):
    # deploy/monitoring/dashboards/*.json are written by scripts/dashboards.py; edit the script.
    import importlib.util

    spec = importlib.util.spec_from_file_location("dashboards", ROOT / "scripts" / "dashboards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path)
    for path in mod.write():
        shipped = ROOT / "deploy" / "monitoring" / "dashboards" / path.name
        assert shipped.read_text() == path.read_text(), f"run: python3 scripts/dashboards.py ({path.name})"
        board = json.loads(path.read_text())
        assert all(t["datasource"]["uid"] == "prometheus" for p in board["panels"] for t in p.get("targets", []))
    assert sorted(p.name for p in (ROOT / "deploy" / "monitoring" / "dashboards").glob("*.json")) == sorted(
        p.name for p in tmp_path.glob("*.json"))


def test_grafana_never_starts_with_the_default_password():
    grafana = compose_service("grafana")
    assert "GF_SECURITY_ADMIN_PASSWORD:" not in grafana and ":-admin" not in grafana
    assert "admin-password.txt" in grafana and 'exec /run.sh "$$@"' in grafana

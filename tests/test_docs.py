"""Keep the docs and the deploy files in step with each other."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def test_relative_links_and_images_exist():
    broken = []
    for doc in DOCS:
        for target in LINK_RE.findall(doc.read_text()):
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
    missing_compose = [s for s in settings if "${" + s not in compose]
    missing_docs = [s for s in settings if f"`{s}`" not in config_doc]
    assert not missing_compose, f"not used in compose.yaml: {missing_compose}"
    assert not missing_docs, f"not documented in docs/configuration.md: {missing_docs}"


def test_env_example_has_no_inline_comments():
    # Docker Compose reads "KEY=   # note" as the value "# note".
    bad = [line for line in (ROOT / ".env.example").read_text().splitlines() if re.match(r"^[A-Z_]+=.*#", line)]
    assert not bad, bad


def compose_service(name: str) -> str:
    text = (ROOT / "compose.yaml").read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|\Z)", text, flags=re.M | re.S)
    assert match, name
    return match.group(1)


def test_containers_stay_locked_down():
    for name in ("kia", "web"):
        service = compose_service(name)
        assert "read_only: true" in service and "- ALL" in service and "no-new-privileges:true" in service, name
    kia = compose_service("kia")
    assert "- ./:/data:ro" in kia  # kia's own code and settings can't be changed from inside
    web_mounts = re.findall(r"^\s+- (\./\S+)", compose_service("web"), flags=re.M)
    # The web server reads only the results, its own settings, and the certificate.
    assert all(m.startswith(("./output:", "./deploy/", "./tls:")) and m.endswith(":ro") for m in web_mounts), web_mounts
    for folder in ("output", "state", "work", "vendor", "profiles", "tls"):
        assert f"- ./{folder}:/data/{folder}" in kia
        assert (ROOT / folder / ".gitkeep").exists(), f"{folder}/ must exist so Docker doesn't create it as root"


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

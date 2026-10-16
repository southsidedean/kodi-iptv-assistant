"""Keep the docs and the deploy files in step with each other."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
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

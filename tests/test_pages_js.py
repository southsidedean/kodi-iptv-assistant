"""The Dashboard's scripts: nothing else in the tests runs them, so a syntax slip would
only show up in a browser."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "kia"


def scripts() -> list[tuple[str, str]]:
    """Every <script> in the strings kia's modules hold, exactly as the pages get them."""
    import importlib

    found = []
    for path in sorted(SRC.glob("*.py")):
        if "<script>" not in path.read_text():
            continue
        module = importlib.import_module(f"kia.{path.stem}")
        for name, value in sorted(vars(module).items()):
            if isinstance(value, str) and "<script>" in value.lower():
                for i, body in enumerate(re.findall(r"<script>(.*?)</script>", value, re.S | re.I)):
                    found.append((f"{path.stem}.{name}#{i}", body))
    return found


def test_there_are_scripts_to_check():
    assert len(scripts()) >= 8


@pytest.mark.parametrize(("where", "body"), scripts(), ids=[w for w, _ in scripts()])
def test_each_script_parses(where: str, body: str, tmp_path: Path):
    if not shutil.which("node"):
        pytest.skip("needs Node.js")
    js = tmp_path / "page.js"
    js.write_text(body)
    result = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{where}: {result.stderr}"


def test_hidden_means_hidden_and_copy_works_without_https():
    from kia import htmlpage, status

    assert "[hidden] { display: none !important; }" in htmlpage.STYLE
    assert 'execCommand("copy")' in status.COPY_SCRIPT and "isSecureContext" in status.COPY_SCRIPT

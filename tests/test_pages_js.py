"""The Dashboard's scripts: nothing else in the tests runs them, so a syntax slip would
only show up in a browser."""

import os
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
        # On CI a skip would hide a page that no longer works in a browser: fail instead.
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Node.js")
    js = tmp_path / "page.js"
    js.write_text(body)
    result = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{where}: {result.stderr}"


def test_hidden_means_hidden_and_copy_works_without_https():
    from kia import htmlpage, status

    assert "[hidden] { display: none !important; }" in htmlpage.STYLE
    assert 'execCommand("copy")' in status.COPY_SCRIPT and "isSecureContext" in status.COPY_SCRIPT


def test_the_scripts_handle_what_the_second_review_found():
    from kia import editor, sourceeditor

    src = "".join(v for v in vars(sourceeditor).values() if isinstance(v, str) and "<script>" in v)
    ed = "".join(v for v in vars(editor).values() if isinstance(v, str) and "<script>" in v)
    # Counting by name sends only a password kia has taken, never each keystroke's.
    assert 'api("POST", "preview", s, getPassword())' in ed and "pass.value || getPassword()" not in ed
    # kia restarting (502 from nginx) isn't "editing isn't available".
    assert ed.count("it may be restarting") == 2 and "it may be restarting" in src
    # A check whose result kia lost in a restart says so, not "0 streams".
    assert "kia restarted, so the check stopped" in src
    # The reason a password didn't unlock the Sources page comes from kia.
    assert 'locked || "Wrong password."' in src
    assert '[name, " ", cnt]' in src  # "Canada 3 of 5 on", not "Canada3 of 5 on"
    assert 'el("p", { role: "status", hidden: true })' not in ed  # status lines are there from the start
    assert 'current.value ? "That\'s not the current password.' in ed

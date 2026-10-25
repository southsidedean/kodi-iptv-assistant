"""scripts/pinned-images.py: the weekly check for newer releases of images compose.yaml pins."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pinned_images", ROOT / "scripts" / "pinned-images.py")
pinned_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pinned_images)


def test_it_finds_the_pinned_images_in_compose():
    found = pinned_images.pinned((ROOT / "compose.yaml").read_text())
    assert "KIA_THREADFIN_IMAGE" in found and "KIA_DCGM_IMAGE" in found
    assert "KIA_NGINX_IMAGE" not in found  # nginx:alpine follows its newest release already
    assert all(image.rpartition(":")[2][:1].isdigit() for image in found.values())


def test_only_a_newer_tag_of_the_same_shape_counts():
    tags = ["latest", "1.2.38-nvidia", "1.2.38", "1.2.37", "1.2.36", "1.10.0"]
    assert pinned_images.newer("1.2.37", tags) == ["1.10.0", "1.2.38"]  # by number, not as text
    dcgm = ["4.6.1-4.8.4", "4.6.1-4.8.4-distroless", "4.5.2-4.8.1-ubuntu22.04", "4.3.1-4.4.1-ubuntu22.04"]
    assert pinned_images.newer("4.3.1-4.4.1-ubuntu22.04", dcgm) == ["4.5.2-4.8.1-ubuntu22.04"]


def test_it_fails_when_one_is_behind_and_passes_when_none_is(tmp_path: Path, capsys):
    compose = tmp_path / "compose.yaml"
    compose.write_text("    image: ${KIA_A_IMAGE:-me/a:1.0.0}\n    image: ${KIA_B_IMAGE:-quay.io/me/b:2.0}\n")
    assert pinned_images.main(str(compose), tags_of=lambda name: ["1.0.1", "1.0.0"]) == 1
    out = capsys.readouterr().out
    assert "me/a:1.0.0 has a newer release: me/a:1.0.1" in out and "quay.io/me/b:2.0 is on another registry" in out
    assert pinned_images.main(str(compose), tags_of=lambda name: ["latest", "1.0.0"]) == 0

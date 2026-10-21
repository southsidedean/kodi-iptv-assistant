import json
from pathlib import Path

from kia import threadfin


def test_new_threadfins_get_kias_playlist_guide_filter_and_options(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KIA_THREADFIN_PLAYLIST", raising=False)
    monkeypatch.setenv("KIA_THREADFIN_2_PLAYLIST", "sports")
    out = tmp_path / "output"
    out.mkdir()
    (out / "plex-1.m3u").write_text("#EXTM3U\n")  # two tuners, named plex-1 and plex-2
    said = []
    assert threadfin.set_up(tmp_path, out, said.append) == ["threadfin", "threadfin-2"]
    one = json.loads((tmp_path / "threadfin" / "conf" / "settings.json").read_text())
    m3u = next(iter(one["files"]["m3u"].values()))
    xmltv = next(iter(one["files"]["xmltv"].values()))
    assert m3u["file.source"] == "http://web/plex-1.m3u" and m3u["buffer"] == "ffmpeg" and m3u["tuner"] == 4
    assert xmltv["file.source"] == "http://web/plex-1.guide.xml.gz"
    assert one["version"] and one["epgSource"] == "XEPG" and one["filter"]["0"]["filter"] == " "
    assert one["ffmpeg.options"] == threadfin.FFMPEG_OPTIONS and "-map" not in one["ffmpeg.options"]
    two = json.loads((tmp_path / "threadfin-2" / "conf" / "settings.json").read_text())
    assert next(iter(two["files"]["m3u"].values()))["file.source"] == "http://web/sports.m3u"
    # A playlist that doesn't exist yet: it says how to finish.
    assert "no playlist named sports" in said[1] and "restart threadfin-2" in said[1]


def test_only_the_threadfins_that_run_are_set_up(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,plex-server")
    assert threadfin.set_up(tmp_path, tmp_path, lambda m: None) == ["threadfin"]
    assert not (tmp_path / "threadfin-2" / "conf" / "settings.json").exists()


def test_existing_threadfin_settings_are_never_changed(tmp_path: Path):
    conf = tmp_path / "threadfin" / "conf"
    conf.mkdir(parents=True)
    (conf / "settings.json").write_text('{"mine": true}')
    assert threadfin.set_up(tmp_path, tmp_path, lambda m: None) == ["threadfin-2"]
    assert (conf / "settings.json").read_text() == '{"mine": true}'


def test_the_default_playlist_is_plex_unless_only_plex_1_exists(tmp_path: Path):
    assert threadfin.playlist_for(1, tmp_path) == "plex"
    (tmp_path / "plex-1.m3u").write_text("")
    assert threadfin.playlist_for(1, tmp_path) == "plex-1"
    (tmp_path / "plex.m3u").write_text("")
    assert threadfin.playlist_for(1, tmp_path) == "plex"
    assert threadfin.playlist_for(2, tmp_path) == "plex-2"


def test_compose_sets_threadfin_up_before_it_starts():
    compose = (Path(__file__).resolve().parent.parent / "compose.yaml").read_text()
    setup = compose[compose.index("  threadfin-setup:"):compose.index("\n  threadfin:")]
    assert 'command: ["threadfin-setup"]' in setup and "./threadfin/conf:/data/threadfin/conf" in setup
    assert compose.count("condition: service_completed_successfully") == 2  # both Threadfins wait for it

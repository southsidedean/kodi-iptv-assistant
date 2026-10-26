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
    note = next(m for m in said if m.startswith("threadfin-2: set up"))
    assert "no playlist named sports" in note and "restart threadfin-2" in note


def test_only_the_threadfins_that_run_are_set_up(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIA_COMPOSE_PROFILES", "plex,plex-server")
    assert threadfin.set_up(tmp_path, tmp_path, lambda m: None) == ["threadfin"]
    assert not (tmp_path / "threadfin-2" / "conf" / "settings.json").exists()


def test_existing_threadfin_settings_are_kept_and_it_gets_a_login(tmp_path: Path):
    conf = tmp_path / "threadfin" / "conf"
    conf.mkdir(parents=True)
    (conf / "settings.json").write_text('{"mine": true, "authentication.pms": false}')
    assert threadfin.set_up(tmp_path, tmp_path, lambda m: None) == ["threadfin-2"]
    # Only the page's login is new; Plex's access stays open.
    assert json.loads((conf / "settings.json").read_text()) == {"mine": True, "authentication.pms": False, "authentication.web": True}


def test_the_login_is_threadfins_own_kind(tmp_path: Path):
    import base64
    import hashlib
    import hmac

    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "settings.json").write_text("{}")
    assert threadfin.secure_login(conf, "threadfin", lambda m: None)
    password = (conf / "admin-password.txt").read_text().strip()
    assert len(password) >= 20 and (conf / "admin-password.txt").stat().st_mode & 0o077 == 0
    db = json.loads((conf / "authentication.json").read_text())
    (uid, user), = db["users"].items()

    def threadfin_hash(secret):  # Threadfin 1.2.37, src/internal/authentication: SHA256()
        return base64.b64encode(hmac.new(secret.encode(), b"_remote_db", hashlib.sha256).digest()).decode()

    assert uid == user["_id"] and uid.startswith("id-") and len(uid) == 13 and len(user["_salt"]) == 20
    assert user["_username"] == threadfin_hash("admin") and user["_password"] == threadfin_hash(password)
    assert user["data"]["authentication.web"] and user["data"]["authentication.pms"] and not user["data"]["authentication.api"]
    assert (conf / "authentication.json").stat().st_mode & 0o077 == 0
    # A Threadfin whose login someone set up already is left alone.
    before = (conf / "authentication.json").read_text()
    assert not threadfin.secure_login(conf, "threadfin", lambda m: None)
    assert (conf / "authentication.json").read_text() == before


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


def test_a_login_made_half_way_is_finished_and_bad_settings_dont_stop_it(tmp_path: Path):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "settings.json").write_text('{"mine": true}')
    threadfin.secure_login(conf, "threadfin", lambda m: None)
    (conf / "settings.json").write_text('{"mine": true}')  # stopped before the page's login was on
    (conf / threadfin.LOGIN_DONE).unlink()
    assert threadfin.secure_login(conf, "threadfin", lambda m: None) is False  # no new user
    assert json.loads((conf / "settings.json").read_text())["authentication.web"] is True
    said = []
    (conf / threadfin.LOGIN_DONE).unlink()
    (conf / "settings.json").write_text("{not json")
    threadfin.secure_login(conf, "threadfin", said.append)
    assert any("couldn't read conf/settings.json" in m for m in said)


def test_a_page_login_you_turn_off_stays_off(tmp_path: Path):
    # threadfin-setup runs before Threadfin at every start: once kia has turned the page's login
    # on, turning it off on Threadfin's own settings page must stick.
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "settings.json").write_text("{}")
    threadfin.secure_login(conf, "threadfin", lambda m: None)
    (conf / "settings.json").write_text('{"authentication.web": false}')
    threadfin.secure_login(conf, "threadfin", lambda m: None)
    assert json.loads((conf / "settings.json").read_text())["authentication.web"] is False

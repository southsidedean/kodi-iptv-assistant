# Contributing

Thanks for helping. Bug reports, ideas, docs fixes, and code are all welcome.

## Getting set up

kia is Python with no dependencies outside the standard library. To run the tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The same checks CI runs on the code, for bugs and security problems:

```sh
.venv/bin/ruff check src tests scripts tools
.venv/bin/bandit -q -c pyproject.toml -r src
shellcheck scripts/*.sh deploy/*.sh
.venv/bin/zizmor --offline .github/workflows
```

The test and lint tools are pinned to exact versions in `pyproject.toml`'s `dev` extra, so a new release of one can't fail an unrelated pull request; Dependabot proposes updates once a month. CI runs the tests on Linux with Python 3.11 and the newest Python, and on macOS. The shell scripts are plain POSIX sh: their tests run them with stand-ins for docker, uname, nc, and curl, under Ubuntu's dash on CI and macOS's sh on the Mac job.

CI also scans the Docker image for known vulnerabilities (CVEs) with [Trivy](https://trivy.dev), and fails on fixable high or critical ones. Known exceptions, each with a reason and an end date, are in `.trivyignore`. [Grype](https://github.com/anchore/grype) gives a second opinion, for reading only.

`scripts/smoke-test.sh` starts kia's whole stack with a test stream, runs a full refresh, and checks what kia serves; CI runs it on every pull request, and you can run it too (it needs Docker and works on a copy of the committed code, so commit first). It uses a fixed commit of iptv-org/epg, so a change there can't block every pull request; each release moves it to iptv-org's newest, and `SMOKE_EPG_REF=latest scripts/smoke-test.sh` tries the newest now. Every week CI also checks kia against the newest iptv-org/epg (`scripts/upstream-check.py`). `tests/test_fuzz.py` feeds odd text through the playlist reader and guide writer.

Keep your own network out of the repository: files, screenshots, commit messages and pull requests. Examples use `192.168.1.20` for kia, `192.0.2.x` for anything else, `my-server.lan` and `you@example.com`. `tests/test_privacy.py` checks the text files. Nothing can check screenshots, so before adding one, look at all of it for addresses, device and account names, and anything personal.

To try a change end to end, run kia with Docker as the [install guide](docs/install.md) describes, then `docker compose up -d --build`.

## Making a change

- Open a pull request against `main`. The tests run automatically and must pass before it can be merged.
- Add tests for new behavior and for bugs you fix.
- Keep the code to the standard library, so installs stay simple.
- Comment the why: what a value means, why the code does something a reader wouldn't expect, and where it guards against untrusted input (playlists, guide sites, other kia servers, files in `state/`). Write comments the way the docs are written.
- Docs are written for people who aren't computer experts: short sentences, plain words, and an example where it helps.
- If users will notice the change, add a line to `CHANGELOG.md` under the next version.
- Never include private details (home addresses, host names, ntfy topics) in code, docs, screenshots, or commit messages.

## Screenshots with a pretend library

For screenshots of Plex or Jellyfin, use a library with nothing personal in it. `tools/mock-media/make_library.py` makes one, laid out the way both expect: 40 public-domain and open movies, 6 public-domain TV series, and 15 public-domain or Creative Commons music artists. Each file is a 10-second placeholder, so the whole library is about 100 MB, and Plex and Jellyfin still show the real posters and summaries. It needs ffmpeg:

```sh
python3 tools/mock-media/make_library.py /path/to/media
```

## Grafana dashboards

kia's Grafana dashboards (`deploy/monitoring/dashboards/*.json`) are written by `scripts/dashboards.py`. To change a panel, edit the script, then run `python3 scripts/dashboards.py`. The tests fail if the JSON files don't match it, if a panel has no note for when it's empty, or if a panel asks for a number kia doesn't publish.

## Diagrams

The docs' diagrams are images, made from Mermaid sources in `docs/diagrams/`. To change one, edit its `.mmd` file and run `scripts/diagrams.sh`, which needs [mermaid-cli](https://github.com/mermaid-js/mermaid-cli). It writes a light and a dark SVG to `images/diagrams/`, and a doc shows them with a `<picture>` tag (copy one from an existing doc). The tests fail if an image doesn't match its source.

Diagrams read best on GitHub as top-to-bottom flowcharts, with the words inside the boxes and short lines broken with `<br>`.

By contributing, you agree that your contribution is licensed under the project's [AGPL-3.0 license](LICENSE).

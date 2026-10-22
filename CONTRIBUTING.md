# Contributing

Thanks for helping. Bug reports, ideas, docs fixes, and code are all welcome.

## Getting set up

kia is Python with no dependencies outside the standard library. To run the tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The same checks CI runs on the code, which find bugs and security problems rather than style:

```sh
.venv/bin/ruff check src tests
.venv/bin/bandit -q -c pyproject.toml -r src
shellcheck scripts/*.sh deploy/*.sh
```

CI also scans the Docker image for known vulnerabilities (CVEs) with [Trivy](https://trivy.dev), and fails on fixable high or critical ones. Known exceptions, each with a reason and an end date, are in `.trivyignore`.

Keep your own network out of the repository: files, screenshots, commit messages and pull requests. Examples use `192.168.1.20` for kia, `192.0.2.x` for anything else, `my-server.lan` and `you@example.com`. `tests/test_privacy.py` checks the text files. Nothing can check screenshots, so before adding one, look at all of it for addresses, device and account names, and anything personal.

To try a change end to end, run kia with Docker as the [install guide](docs/install.md) describes, then `docker compose up -d --build`.

## Making a change

- Open a pull request against `main`. The tests run automatically and must pass before it can be merged.
- Add tests for new behavior and for bugs you fix.
- Keep the code to the standard library, so installs stay simple.
- Docs are written for people who aren't computer experts: short sentences, plain words, and an example where it helps.
- If users will notice the change, add a line to `CHANGELOG.md` under the next version.
- Never include private details (home addresses, host names, ntfy topics) in code, docs, screenshots, or commit messages.

## Diagrams

The docs' diagrams are images, made from Mermaid sources in `docs/diagrams/`. To change one, edit its `.mmd` file and run `scripts/diagrams.sh`, which needs [mermaid-cli](https://github.com/mermaid-js/mermaid-cli). It writes a light and a dark SVG to `images/diagrams/`, and a doc shows them with a `<picture>` tag (copy one from an existing doc). The tests fail if an image doesn't match its source.

Diagrams read best on GitHub as top-to-bottom flowcharts, with the words inside the boxes and short lines broken with `<br>`.

By contributing, you agree that your contribution is licensed under the project's [AGPL-3.0 license](LICENSE).

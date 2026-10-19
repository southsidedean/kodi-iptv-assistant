# Contributing

Thanks for helping. Bug reports, ideas, docs fixes, and code are all welcome.

## Getting set up

kia is Python with no dependencies outside the standard library. To run the tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

To try a change end to end, run kia with Docker as the [install guide](docs/install.md) describes, then `docker compose up -d --build`.

## Making a change

- Open a pull request against `main`. The tests run automatically and must pass before it can be merged.
- Add tests for new behavior and for bugs you fix.
- Keep the code to the standard library, so installs stay simple.
- Docs are written for people who aren't computer experts: short sentences, plain words, and an example where it helps.
- If users will notice the change, add a line to `CHANGELOG.md` under the next version.
- Never include private details (home addresses, host names, ntfy topics) in code, docs, screenshots, or commit messages.

By contributing, you agree that your contribution is licensed under the project's [AGPL-3.0 license](LICENSE).

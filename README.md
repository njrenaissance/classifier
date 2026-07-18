# classifier

Generated from the `basic` cookiecutter template.
A minimal Python project, managed with [uv](https://docs.astral.sh/uv/).

## Structure

```bash
├── src/
│   └── main.py       # greet()
├── tests/
│   └── test_main.py  # test for greet()
└── pyproject.toml
```

## Setup

This project uses `uv` for package management, linting, and formatting.

```bash
uv sync
```

## Wiki

This project keeps an `openwiki/` folder of generated codebase documentation
(produced by [OpenWiki](https://www.npmjs.com/package/openwiki)). It is
generated output — **never hand-edit it**. A scheduled CI workflow
(`.github/workflows/openwiki-update.yml`) keeps `openwiki/` current
automatically: it regenerates the wiki daily on the Anthropic provider and opens
a single rolling review PR — no auto-merge (see
[ADR-0011](spec/adr/0011-openwiki-ci-regeneration.md)). The top-level
`openwiki/GENERATED.md` records when the wiki was last regenerated and from which
commit, so you can tell how current it is.

OpenWiki is a per-machine global CLI, **not** a project dependency (it is never
added to `pyproject.toml`). Regenerating in the same PR as a code change is
optional now that CI is the primary path, but if you want the docs updated
immediately, install and authenticate the CLI once, then regenerate:

```bash
npm install -g openwiki    # one-time, per machine
openwiki auth <provider>   # one-time: sets up the LLM provider + API key
openwiki code --init       # first run in a fresh repo
openwiki code --update     # regenerate on demand
```

Regenerating calls a paid LLM provider. See `.claude/standards/wiki.md` for the
rules agents follow.

## Run

```bash
uv run python src/main.py
```

## Test

```bash
uv run pytest
```

## Lint

```bash
uv run ruff check .
```

## Format

```bash
uv run ruff format .
```

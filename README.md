# classifier

Generated from the `basic` cookiecutter template.
A minimal Python project, managed with [uv](https://docs.astral.sh/uv/).

## Structure

```bash
├── src/
│   └── main.py       # CLI entry point (local end-to-end)
├── tests/
│   └── test_main.py  # tests for the CLI
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
[ADR-0011](spec/adr/0011-openwiki-ci-regeneration.md)). OpenWiki records its own
provenance in `openwiki/.last-update.json` (`updatedAt`, `gitHead`, `model`), so
you can tell how current the wiki is and which commit it was built from.

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

Point the CLI at a local source (a file or a directory) plus a category
Markdown file; it writes a CSV of `filename,category,confidence`:

```bash
export ANTHROPIC_API_KEY=sk-...   # required
uv run python src/main.py ./docs -c categories.md -o results.csv
```

`source` is positional; `-c/--categories` and `-o/--output` are required.
Unsupported files in a directory are skipped with a warning, and a file that
fails extraction or classification is skipped so the rest of the batch still
completes.

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

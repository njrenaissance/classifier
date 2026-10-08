# classifier

Minimal Python project managed with [uv](https://docs.astral.sh/uv/).

## Profile

Cross-cutting concerns enabled for this project:

- App config (`pydantic-settings`): enabled
- Structured logging (`structlog`): disabled
- Telemetry (OpenTelemetry): disabled
- Security scanning (`bandit`): disabled
- Diagramming (`diagrams` library): disabled

## Imports

- @.claude/standards/git-workflow.md
- @.claude/standards/decisions.md
- @.claude/standards/wiki.md
- @.claude/standards/testing.md
- @.claude/standards/error-handling.md
- @.claude/standards/database.md
- @.claude/standards/configuration.md
- @.claude/standards/logging.md

## Structure

```text
├── src/
│   └── main.py       # CLI entry point (local end-to-end)
├── tests/
│   └── test_main.py  # tests for the CLI
└── pyproject.toml
```

## Commands

```bash
make setup                 # one-time: install deps + local Git hooks
uv sync                    # install dependencies
uv run python src/main.py  # run
uv run pytest              # test
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src            # type-check
uv run pre-commit run --all-files  # run all Git hooks manually
uvx cruft check            # is this project behind its template?
```

## Git hooks

Local quality gates run through the [`pre-commit`](https://pre-commit.com/)
framework (config in `.pre-commit-config.yaml`). `make setup` installs them (Git
can't auto-install hooks on clone, so this is a one-time step); or run them
directly:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
```

Then `git commit` runs `ruff format --check`, `ruff check`, and
`mypy src`;
`git push` runs `pytest`. A failing hook is the same signal `ci.yml` would give,
just earlier — and `ci.yml` runs the same checks regardless, so it stays the real
gate even if the local hooks aren't installed.

## Template sync

This project is linked to the cookiecutter template it was generated from via
`.cruft.json` (the template URL, the exact template commit, and the answers given
at generation). `.cruft.json` is the authoritative record of template lineage —
there is no separate version file. `uvx cruft check` reports whether the template
has moved ahead; the on-demand `Template Sync` workflow
(`.github/workflows/template-sync.yml`) runs the same check in CI when you trigger
it. To pull template changes in, use the **`update-from-template`** skill
(`.claude/skills/update-from-template/`), which runs `cruft update`, resolves any
conflicts, and re-runs the gate above. A project that somehow lost its `.cruft.json`
can re-establish the link with the **`link-to-template`** skill.

## Conventions

All code must follow Clean Code principles (Robert C. Martin) — no exceptions.

Where applicable, apply the 23 Gang of Four design patterns (*Design Patterns: Elements of Reusable Object-Oriented Software*) rather than ad-hoc structures:

- **Creational**: Abstract Factory, Builder, Factory Method, Prototype, Singleton
- **Structural**: Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
- **Behavioral**: Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

Don't force a pattern where a plain function or class is simpler — use these to name and structure a design once the problem actually calls for one.

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`, `pytest-rules.md`) and load automatically when Claude touches matching files.

Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` before considering a change done — the installed Git hooks (see **Git hooks** above) enforce the same checks at commit/push time, so don't bypass them with `--no-verify`.

## Database migrations

Migrations are **fix-forward only** — we never roll a migration backwards. Every
Alembic revision's `downgrade()` must `raise NotImplementedError` rather than
attempt a reversal; a mistake in a released migration is corrected by writing a
new forward migration, not by downgrading. The `alembic/script.py.mako` template
already scaffolds new revisions this way.

<!-- OPENWIKI:START -->

## OpenWiki

See [AGENTS.md](AGENTS.md) for OpenWiki agent instructions.

<!-- OPENWIKI:END -->

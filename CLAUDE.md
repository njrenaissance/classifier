# classifier

Minimal Python project managed with [uv](https://docs.astral.sh/uv/).

## Profile

Cross-cutting concerns enabled for this project:

- App config (`pydantic-settings`): enabled
- Structured logging (`structlog`): disabled
- Telemetry (OpenTelemetry): disabled
- Security scanning (`bandit`): disabled

## Imports

- @.claude/standards/git-workflow.md
- @.claude/standards/wiki.md
- @.claude/standards/testing.md
- @.claude/standards/error-handling.md
- @.claude/standards/configuration.md
- @.claude/standards/logging.md

## Structure

```text
├── src/
│   └── main.py       # greet()
├── tests/
│   └── test_main.py  # test for greet()
└── pyproject.toml
```

## Commands

```bash
uv sync                    # install dependencies
uv run python src/main.py  # run
uv run pytest              # test
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src            # type-check
```

## Conventions

All code must follow Clean Code principles (Robert C. Martin) — no exceptions.

Where applicable, apply the 23 Gang of Four design patterns (*Design Patterns: Elements of Reusable Object-Oriented Software*) rather than ad-hoc structures:

- **Creational**: Abstract Factory, Builder, Factory Method, Prototype, Singleton
- **Structural**: Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
- **Behavioral**: Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

Don't force a pattern where a plain function or class is simpler — use these to name and structure a design once the problem actually calls for one.

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`, `pytest-rules.md`) and load automatically when Claude touches matching files.

Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` before considering a change done.

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

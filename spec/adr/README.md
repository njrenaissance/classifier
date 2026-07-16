# Architecture Decision Records

Each decision with meaningful tradeoffs gets its own file (`NNNN-<slug>.md`). ADRs are append-only: a changed decision is superseded by a new ADR, not edited in place. See `coding-workflow.md` → *Architecture Decision Records* for the convention.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-llm-based-classification.md) | LLM-based classification (vs rules / classical ML) | accepted |
| [0002](0002-model-haiku-4-5.md) | Model: Claude Haiku 4.5 | accepted |
| [0003](0003-cli-batch-interface.md) | CLI / batch interface (vs library / service) | accepted |
| [0004](0004-csv-file-output.md) | CSV file output (vs database) | accepted |
| [0005](0005-confidence-self-consistency.md) | Confidence via self-consistency; reserved `unknown` category | accepted |
| [0006](0006-text-extraction-per-format-libs.md) | Per-format Python text extraction (`.doc` handling deferred → ADR-0009) | accepted |
| [0007](0007-sharepoint-app-only-auth.md) | SharePoint access via app-only (client-credentials) auth | accepted |
| [0008](0008-prompt-structured-output.md) | Prompt shape & structured output (label-only enum, cached prefix) | accepted |
| [0009](0009-defer-legacy-doc-extraction.md) | Defer legacy `.doc` extraction (PDF/DOCX only for now) | accepted |
| [0010](0010-uniform-document-source.md) | Uniform `DocumentSource` abstraction (local + SharePoint behind one protocol) | accepted |

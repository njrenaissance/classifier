# Sample documents for the live-fire stack

This directory is the **default** mount for the live-fire compose stack — it is
bind-mounted read-only into the walker and processor at `/data`
(`CLASSIFIER__FILESYSTEM_ROOT=/data`).

Drop a few small `.pdf` / `.docx` files here to classify, **or** point the stack at
another directory without touching this one by setting `SAMPLE_DOCS`:

```bash
# Windows (PowerShell): use your own documents folder
$env:SAMPLE_DOCS = "C:\Users\jon_m\testdocs"
docker compose -f infra/docker-compose.yml up
```

Only files with a registered extractor are enumerated (currently `.pdf` and
`.docx` — see `src/extraction.py`); anything else is skipped with a warning.

See [`../README.md`](../README.md) for the full runbook.

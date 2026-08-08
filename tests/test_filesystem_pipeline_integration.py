"""End-to-end integration test for the filesystem source (ADR-0020).

Exercises the real ``real files → real queue → real PostgreSQL`` leg of the
pipeline with **no** Graph/SharePoint anywhere: a :class:`~filesystem_walker.FilesystemWalker`
enumerates a temp directory of valid PDFs, hashes them, UPSERTs ``documents`` rows
and enqueues work items onto a real Azurite queue; a :class:`~processor.Processor`
wired with a :class:`~content_source.FilesystemContentSource` then consumes each
message, reads the bytes from disk, extracts text, and UPSERTs a ``completed``
result. Only the self-consistency voter is faked — real LLM verdicts are reserved
for the manual live-fire run (``infra/``), which costs money.

Both containers are ``testcontainers``-managed and the whole module skips when
Docker is unavailable, matching ``tests/test_db.py``. PostgreSQL is brought up via
the real Alembic migration (``alembic upgrade head``), Azurite via a generic
container exposing its queue port.
"""

import contextlib
from collections.abc import Iterator

import pytest
from alembic.config import Config
from azure.core.exceptions import ResourceExistsError
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from db import Document, DocumentStatus
from message_queue import MessageQueue

pytestmark = pytest.mark.integration

# The well-known Azurite dev-storage account (fixed credentials, not a secret).
_AZURITE_ACCOUNT = "devstoreaccount1"
_AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
_QUEUE_NAME = "classifier-work-items"


def _build_pdf(text: str) -> bytes:
    """Build a minimal single-page PDF whose only content is ``text`` (real pypdf path)."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 24 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    return bytes(out)


class _FakeVoter:
    """A deterministic stand-in for the self-consistency voter — no LLM call."""

    def classify(self, _text: str):
        from self_consistency import Verdict

        return Verdict(category="contract", confidence=0.9)


def _skip_without_docker(factory):
    """Start a container from ``factory``, skipping the module when Docker is absent."""
    docker_error = pytest.importorskip("docker.errors").DockerException
    try:
        container = factory()
        container.start()
    except docker_error as exc:
        pytest.skip(f"Docker unavailable: {exc}")
    return container


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """A Postgres brought up via ``alembic upgrade head`` (the real migration path)."""
    postgres_cls = pytest.importorskip("testcontainers.postgres").PostgresContainer
    postgres = _skip_without_docker(lambda: postgres_cls("postgres:16-alpine", driver="psycopg"))
    monkeypatch = pytest.MonkeyPatch()
    try:
        url = postgres.get_connection_url()
        monkeypatch.setenv("CLASSIFIER__DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()
    finally:
        monkeypatch.undo()
        postgres.stop()


@pytest.fixture(scope="module")
def queue_connection_string() -> Iterator[str]:
    """A running Azurite queue endpoint; yields a connection string for its queue."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    def _factory() -> DockerContainer:
        container = DockerContainer("mcr.microsoft.com/azure-storage/azurite:latest")
        container = container.with_exposed_ports(10001)
        return container.with_command("azurite-queue --queueHost 0.0.0.0 --queuePort 10001 --skipApiVersionCheck")

    azurite = _skip_without_docker(_factory)
    try:
        wait_for_logs(azurite, "Queue service successfully listens", timeout=30)
        host = azurite.get_container_host_ip()
        port = azurite.get_exposed_port(10001)
        yield (
            f"DefaultEndpointsProtocol=http;AccountName={_AZURITE_ACCOUNT};AccountKey={_AZURITE_KEY};"
            f"QueueEndpoint=http://{host}:{port}/{_AZURITE_ACCOUNT};"
        )
    finally:
        azurite.stop()


def _make_queue(connection_string: str) -> MessageQueue:
    """A real MessageQueue over Azurite, creating the queue if it does not exist."""
    from azure.storage.queue import QueueClient

    client = QueueClient.from_connection_string(connection_string, _QUEUE_NAME)
    with contextlib.suppress(ResourceExistsError):  # a re-run may find the queue already created
        client.create_queue()
    return MessageQueue(client)


def test_filesystem_pipeline_walks_enqueues_and_classifies(migrated_engine, queue_connection_string, tmp_path):
    from content_source import FilesystemContentSource
    from filesystem_walker import FilesystemWalker
    from processor import Processor
    from writer import DatabaseWriter

    # A small tree of valid PDFs to classify (real extraction path).
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "alpha.pdf").write_bytes(_build_pdf("An agreement between the parties."))
    (root / "sub" / "beta.pdf").write_bytes(_build_pdf("This lease is made effective today."))

    # --- produce: walk the filesystem, enqueue work items, write queued rows ---
    with _make_queue(queue_connection_string) as producer_queue, Session(migrated_engine) as walk_session:
        status = FilesystemWalker(walk_session, producer_queue, root).walk()
    assert status.value == "completed"

    with Session(migrated_engine) as check:
        queued = check.scalars(select(Document).where(Document.status == DocumentStatus.queued)).all()
    assert {doc.drive_item_id for doc in queued} == {"alpha.pdf", "sub/beta.pdf"}

    # --- consume: process each message via the filesystem retrieval seam ---
    source = FilesystemContentSource(root)
    with _make_queue(queue_connection_string) as consumer_queue:
        for _ in range(len(queued)):
            with Session(migrated_engine) as work_session:
                Processor(work_session, source, consumer_queue, _FakeVoter(), DatabaseWriter(work_session)).run_once()
        # The queue is drained — a further receive finds nothing.
        assert consumer_queue.receive() is None

    with Session(migrated_engine) as check:
        rows = check.scalars(select(Document)).all()
        by_item = {doc.drive_item_id: doc for doc in rows}
    assert set(by_item) == {"alpha.pdf", "sub/beta.pdf"}
    for doc in by_item.values():
        assert doc.status is DocumentStatus.completed
        assert doc.category == "contract"
        assert doc.confidence == 0.9

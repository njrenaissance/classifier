from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from models import Message

pytestmark = pytest.mark.unit

_ENQUEUED_AT = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _fields(**overrides: object) -> dict[str, object]:
    """A complete, valid set of ``Message`` fields; ``overrides`` replaces any."""
    fields: dict[str, object] = {
        "document_id": 42,
        "sync_state_id": 1,
        "drive_id": "drive-abc",
        "drive_item_id": "item-xyz",
        "file_name": "contract.pdf",
        "mime_type": "application/pdf",
        "matter_folder": "Acme",
        "content_hash": "sha256-deadbeef",
        "enqueued_at": _ENQUEUED_AT,
    }
    fields.update(overrides)
    return fields


def _message(**overrides: object) -> Message:
    """A valid ``Message`` for round-trip and success-path assertions."""
    return Message(**_fields(**overrides))  # type: ignore[arg-type]


def test_json_round_trip_reproduces_the_original():
    message = _message()

    assert Message.model_validate_json(message.model_dump_json()) == message


@pytest.mark.parametrize(
    "missing_field",
    [
        pytest.param("document_id", id="absent_document_id"),
        pytest.param("sync_state_id", id="absent_sync_state_id"),
        pytest.param("drive_id", id="absent_drive_id"),
        pytest.param("drive_item_id", id="absent_drive_item_id"),
        pytest.param("file_name", id="absent_file_name"),
        pytest.param("mime_type", id="absent_mime_type"),
        pytest.param("matter_folder", id="absent_matter_folder"),
        pytest.param("content_hash", id="absent_content_hash"),
        pytest.param("enqueued_at", id="absent_enqueued_at"),
    ],
)
def test_missing_required_field_raises(missing_field: str):
    fields = _fields()
    del fields[missing_field]

    with pytest.raises(ValidationError):
        Message(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("document_id", "not-an-int", id="document_id_not_int"),
        pytest.param("sync_state_id", "not-an-int", id="sync_state_id_not_int"),
        pytest.param("drive_item_id", 123, id="drive_item_id_not_str"),
        pytest.param("enqueued_at", "not-a-datetime", id="enqueued_at_unparseable"),
    ],
)
def test_mistyped_field_raises(field: str, value: object):
    with pytest.raises(ValidationError):
        Message(**_fields(**{field: value}))  # type: ignore[arg-type]


def test_naive_enqueued_at_is_rejected():
    naive = datetime(2026, 7, 23, 12, 0)  # deliberately timezone-naive

    with pytest.raises(ValidationError):
        Message(**_fields(enqueued_at=naive))  # type: ignore[arg-type]


def test_aware_enqueued_at_is_preserved():
    message = _message(enqueued_at=_ENQUEUED_AT)

    assert message.enqueued_at.tzinfo is not None

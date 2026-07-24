"""Project exception hierarchy.

Every domain failure derives from :class:`AppError`, so a caller can catch
one failure mode without swallowing unrelated ones. Never raise or catch
bare ``Exception`` in this codebase.
"""


class AppError(Exception):
    """Base class for all application-specific errors."""


class CategoryFileError(AppError):
    """The category-definition Markdown file is missing or malformed."""


class SourceError(AppError):
    """A document source path is missing or is not a file or directory."""


class ExtractionError(AppError):
    """Text extraction from a document failed and must not be swallowed."""


class UnsupportedFormatError(ExtractionError):
    """The document's file type has no registered text extractor."""


class OutputError(AppError):
    """Writing the results CSV to the target path failed."""


class ClassificationError(AppError):
    """A classification API call failed or returned an unusable result."""


class PersistenceError(AppError):
    """A database read or write failed and must not be swallowed."""


class GraphError(AppError):
    """A Microsoft Graph auth, HTTP, or response-shape failure.

    Raised at the Graph client boundary so token-acquisition, transport, and
    malformed-response failures surface as one domain type — always chained
    (``raise GraphError(...) from err``) to preserve the original cause.
    """


class QueueError(AppError):
    """An Azure Queue transport, auth, or message-shape failure.

    Raised at the queue client boundary (:mod:`message_queue`) so send/receive
    transport failures and a malformed message body surface as one domain type —
    always chained (``raise QueueError(...) from err``) to preserve the cause.
    """

"""Project exception hierarchy.

Every domain failure derives from :class:`AppError`, so a caller can catch
one failure mode without swallowing unrelated ones. Never raise or catch
bare ``Exception`` in this codebase.
"""


class AppError(Exception):
    """Base class for all application-specific errors."""


class CategoryFileError(AppError):
    """The category-definition Markdown file is missing or malformed."""

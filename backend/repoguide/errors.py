"""Application errors that the API maps to HTTP status codes.

Anything else that escapes a request is a bug and surfaces as a logged 500.
"""


class InvalidInputError(ValueError):
    """The request cannot be processed as given (HTTP 400)."""


class NotFoundError(LookupError):
    """The requested object does not exist (HTTP 404)."""


class ConflictError(RuntimeError):
    """The request conflicts with the current state, e.g. a job is running (HTTP 409)."""

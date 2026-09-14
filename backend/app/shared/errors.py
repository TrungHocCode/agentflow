"""Application errors shared across module boundaries."""


class ApplicationError(Exception):
    """Base error for expected application failures."""

    code = "application_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class ValidationError(ApplicationError):
    """Raised when a domain object fails semantic validation."""

    code = "validation_error"


class ResourceNotFoundError(ApplicationError):
    """Raised when a requested resource does not exist."""

    code = "not_found"


class ConflictError(ApplicationError):
    """Raised when an operation conflicts with current resource state."""

    code = "conflict"


class PersistenceError(ApplicationError):
    """Raised when the application cannot read or write durable state."""

    code = "persistence_unavailable"

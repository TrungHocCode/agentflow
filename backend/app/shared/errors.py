"""Application errors shared across module boundaries."""

import uuid


class ApplicationError(Exception):
    """Base error for expected application failures."""

    code = "application_error"
    category = "application"
    status_code = 400
    retryable = False

    def __init__(
        self,
        message: str,
        code: str | None = None,
        *,
        user_message: str | None = None,
        retryable: bool | None = None,
        entity: str | None = None,
        error_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = user_message or message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.entity = entity
        self.error_id = error_id or str(uuid.uuid4())


class ValidationError(ApplicationError):
    """Raised when a domain object fails semantic validation."""

    code = "validation_error"
    category = "validation"
    status_code = 422


class AuthenticationError(ApplicationError):
    """Raised when a user cannot authenticate or their credential is invalid."""

    code = "authentication_failed"
    category = "authentication"
    status_code = 401


class ResourceNotFoundError(ApplicationError):
    """Raised when a requested resource does not exist."""

    code = "not_found"
    category = "not_found"
    status_code = 404


class ConflictError(ApplicationError):
    """Raised when an operation conflicts with current resource state."""

    code = "conflict"
    category = "conflict"
    status_code = 409


class RateLimitExceeded(ApplicationError):
    """A caller exceeded a shared request-rate allowance."""

    code = "rate_limit_exceeded"
    category = "rate_limit"
    status_code = 429
    retryable = True


class PersistenceError(ApplicationError):
    """Raised when the application cannot read or write durable state."""

    code = "persistence_unavailable"
    category = "dependency"
    status_code = 503
    retryable = True

"""Frozen exact-URL collection policy, including redirects and thread context propagation."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

_SCOPE: ContextVar[frozenset[str] | None] = ContextVar("collection_scope", default=None)


def normalized_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def collection_allowed(value: str) -> bool:
    scope = _SCOPE.get()
    return scope is None or normalized_url(value) in scope


def scoped_collection() -> bool:
    return _SCOPE.get() is not None


@contextmanager
def collection_scope(urls: list[str] | None) -> Iterator[None]:
    token = _SCOPE.set(None if urls is None else frozenset(normalized_url(url) for url in urls))
    try:
        yield
    finally:
        _SCOPE.reset(token)

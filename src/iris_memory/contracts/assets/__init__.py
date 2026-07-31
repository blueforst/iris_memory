"""Helpers for reading packaged contract schemas and fixtures."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path


@contextmanager
def contract_asset(*parts: str) -> Iterator[Path]:
    """Yield a filesystem path for a packaged contract asset."""

    target = files("iris_memory.contracts.assets").joinpath(*parts)
    with as_file(target) as path:
        yield path

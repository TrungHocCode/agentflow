"""Blob retention audit: find run-scoped files no durable record references.

Audit-only by design; deletion is a consequential manual-gate action, never automatic.
Collectors gather referenced URIs from snapshot/brief rows (or read APIs); this module
compares them against the filesystem.
"""

from pathlib import Path


def find_unreferenced_blobs(root: str | Path, referenced_uris: set[str],
                            area: str = "runs") -> tuple[list[str], int]:
    """Return (relative URIs with no referrer, total bytes) under root/area snapshot and brief dirs."""
    base = Path(root)
    unreferenced: list[str] = []
    total_bytes = 0
    for blob_dir in ("snapshots", "briefs"):
        area_dir = base / area
        if not area_dir.is_dir():
            continue
        for path in sorted(area_dir.rglob(f"*/{blob_dir}/*.txt")) + sorted(
                area_dir.rglob(f"*/{blob_dir}/*.md")):
            try:
                relative = path.relative_to(base).as_posix()
            except ValueError:
                continue
            if relative not in referenced_uris:
                unreferenced.append(relative)
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
    return unreferenced, total_bytes

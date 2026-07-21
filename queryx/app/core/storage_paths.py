from __future__ import annotations

from pathlib import Path, PurePosixPath


class StorageReferenceError(ValueError):
    pass


def resolve_storage_reference(reference: str, root: Path, namespace: str) -> Path:
    """Resolve a managed storage reference without depending on the working directory."""
    if not reference or not namespace or "\\" in reference:
        raise StorageReferenceError("Stored file reference is invalid")
    logical = PurePosixPath(reference)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise StorageReferenceError("Stored file reference is invalid")
    if len(logical.parts) == 1:
        filename = logical.parts[0]  # Legacy bindings stored only the filename.
        if reference != filename:
            raise StorageReferenceError("Stored file reference is invalid")
    elif len(logical.parts) == 2 and logical.parts[0] == namespace:
        filename = logical.parts[1]
        if reference != f"{namespace}/{filename}":
            raise StorageReferenceError("Stored file reference is invalid")
    else:
        raise StorageReferenceError("Stored file reference is invalid")
    resolved_root = root.resolve()
    candidate = (resolved_root / filename).resolve()
    if candidate.parent != resolved_root:
        raise StorageReferenceError("Stored file reference is invalid")
    return candidate

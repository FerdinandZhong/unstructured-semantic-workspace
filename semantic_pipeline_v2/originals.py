"""Resolve prepared originals by dataset and pinned manifest identity."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from tempfile import SpooledTemporaryFile
from urllib.parse import urlencode

from .core import v1_repository
from .workspace import EXTRA_SOURCES, FACTORY, FINANCE, ROOT, scoped


BLOCK_SIZE = 64 * 1024
MAX_ORIGINAL_BYTES = 64 * 1024 * 1024
TYPES = {
    ".pdf": ("application/pdf", True),
    ".md": ("text/plain; charset=utf-8", True),
    ".txt": ("text/plain; charset=utf-8", True),
    ".m4a": ("audio/mp4", True),
    ".mp3": ("audio/mpeg", True),
    ".mp4": ("video/mp4", True),
    ".webm": ("video/webm", True),
    ".png": ("image/png", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
}


@dataclass(frozen=True)
class Original:
    root: Path
    relative: Path
    digest: str
    size: int | None

    @property
    def content_type(self):
        return TYPES.get(self.relative.suffix.lower(), ("application/octet-stream", False))


def load_originals():
    # Files outside the repository are optional: V2_LOCAL_ORIGINALS (default ~/Downloads) and the V1 checkout.
    roots = {"downloads": Path(os.environ.get("V2_LOCAL_ORIGINALS") or Path.home() / "Downloads"),
             "project": ROOT, "v1": v1_repository()}
    finance = json.loads((ROOT / FINANCE / "sources.json").read_text())
    factory = json.loads((ROOT / FACTORY / "documents/manifest.json").read_text())
    originals = {}
    for asset in finance["assets"]:
        if asset["id"] == "workbook":
            continue
        key = "jade-adverse-media" if asset["id"] == "jade_news" else asset["id"]
        originals[scoped("financial-services", "source", key)] = Original(
            roots[asset["root"]], Path(asset["path"]), asset["sha256"], asset["bytes"]
        )
    for doc in factory["documents"]:
        originals[scoped("manufacturing", "source", doc["id"])] = Original(
            ROOT / FACTORY, Path(doc["primary_path"]), doc["sha256"], None
        )
    for dataset in ("financial-services", "manufacturing"):   # wider corpus, pinned in the registry
        for item in json.loads((ROOT / EXTRA_SOURCES).read_text())[dataset]:
            originals[scoped(dataset, "source", item["key"])] = Original(ROOT, Path(item["path"]), item["sha256"], None)
    return originals


@contextmanager
def local_file(original):
    """Open an allowlisted regular file without following nested symlinks."""
    if original.relative.is_absolute() or not original.relative.parts or ".." in original.relative.parts:
        raise OSError("Unsafe original path")
    with ExitStack() as stack:
        parent = os.open(original.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        for part in original.relative.parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, parent)
        descriptor = os.open(original.relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Original is not a regular file")
            yield stream


def original_info(dataset, source_id, original, status, size=None):
    mime_type, preview = original.content_type if original else (None, False)
    available = status == "available"
    query = urlencode({"dataset": dataset, "sourceId": source_id})
    return {
        "status": status,
        "filename": original.relative.name if original else None,
        "mimeType": mime_type.split(";", 1)[0] if mime_type else None,
        "bytes": size if available else None,
        "canPreview": available and preview,
        "originalUrl": f"/api/original?{query}" if available else None,
    }


def byte_range(value, size):
    """Return an inclusive single byte range, or reject an unsatisfiable request."""
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", value, re.ASCII)
    if not match or not size or not any(match.groups()):
        raise ValueError("Invalid original byte range")
    start, end = match.groups()
    if not start:
        if not int(end):
            raise ValueError("Empty byte range")
        return max(0, size - int(end)), size - 1
    first = int(start)
    last = min(int(end), size - 1) if end else size - 1
    if first > last:
        raise ValueError("Unsatisfiable byte range")
    return first, last


@contextmanager
def checked_original(original, *, retain=False):
    """Hash a bounded-memory stream and optionally retain exactly the verified bytes."""
    if original is None:
        yield "unsupported", None, None
        return
    with ExitStack() as stack:
        status, size = "missing", 0
        copy = stack.enter_context(SpooledTemporaryFile(max_size=256 * 1024)) if retain else None
        try:
            stream = stack.enter_context(local_file(original))
            limit = min(original.size, MAX_ORIGINAL_BYTES) if original.size is not None else MAX_ORIGINAL_BYTES
            initial_size = os.fstat(stream.fileno()).st_size
            if initial_size > limit or (original.size is not None and initial_size != original.size):
                status = "changed"
            else:
                checksum = sha256()
                for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
                    size += len(block)
                    if size > limit:
                        break
                    checksum.update(block)
                    if copy is not None:
                        copy.write(block)
                status = "available" if (
                    size == initial_size and checksum.hexdigest() == original.digest
                ) else "changed"
        except OSError:
            pass
        if status == "available" and copy is not None:
            copy.seek(0)
        yield status, size, copy

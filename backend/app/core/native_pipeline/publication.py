"""Same-filesystem staged publication with atomic no-clobber linking."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat

from app.core.native_pipeline.errors import OutputAlreadyExists


_COPY_CHUNK_BYTES = 1024 * 1024


def assert_output_absent(destination: Path) -> None:
    """Preflight the no-clobber rule before anything is spawned."""
    if destination.exists() or destination.is_symlink():
        raise OutputAlreadyExists(
            f"the output path {destination} already exists",
            details={"output_path": str(destination)},
        )


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    """A process-owned inode staged beside its final destination."""

    staged: Path
    destination: Path
    size: int

    def discard(self) -> None:
        """Remove only the uniquely named staged inode owned by this run."""
        with suppress(FileNotFoundError):
            self.staged.unlink()

    def commit(self) -> int:
        """Link at the final commit point without any post-commit failure."""
        try:
            os.link(self.staged, self.destination)
        except FileExistsError as exc:
            with suppress(OSError):
                self.discard()
            raise OutputAlreadyExists(
                f"the output path {self.destination} was created during the run",
                details={"output_path": str(self.destination)},
            ) from exc
        except OSError:
            with suppress(OSError):
                self.discard()
            raise

        # The successful link is the irreversible commit point. Cleanup and
        # durability hints after it are best effort: they must never turn a
        # visible artifact into an error result.
        with suppress(OSError):
            self.discard()
        with suppress(OSError):
            _fsync_directory(self.destination.parent)
        return self.size


def prepare_artifact(
    source: Path, destination: Path, *, max_bytes: int | None = None
) -> PreparedPublication:
    """Copy a regular nofollow source into a durable mode-0600 staged inode."""
    staged = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.part"
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(source_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise OSError("publication source is not a non-empty regular file")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise OSError("publication source exceeds its configured byte bound")
        staged_fd = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(staged_fd, 0o600)
            copied = 0
            while True:
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                if not chunk:
                    break
                copied += len(chunk)
                if max_bytes is not None and copied > max_bytes:
                    raise OSError("publication source grew beyond its byte bound")
                view = memoryview(chunk)
                while view:
                    view = view[os.write(staged_fd, view) :]
            if copied != metadata.st_size:
                raise OSError("publication source changed while being copied")
            os.fsync(staged_fd)
        except BaseException:
            with suppress(OSError):
                os.close(staged_fd)
            with suppress(OSError):
                staged.unlink()
            raise
        else:
            # fsync is the durability barrier; a close failure after it cannot
            # invalidate the staged bytes and must not mask the primary result.
            with suppress(OSError):
                os.close(staged_fd)
    finally:
        with suppress(OSError):
            os.close(source_fd)
    return PreparedPublication(staged=staged, destination=destination, size=metadata.st_size)


def publish_artifact(source: Path, destination: Path) -> int:
    """Compatibility convenience: prepare and immediately commit an artifact."""
    return prepare_artifact(source, destination).commit()


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "PreparedPublication",
    "assert_output_absent",
    "prepare_artifact",
    "publish_artifact",
]

"""Private, persistent storage for the ONE active source CV.

Layout (all inside a gitignored private directory that is never exposed through the API):

    <private dir>/source_cv/
        profile.json              the manifest + parsed profile  -- the single COMMIT POINT
        upload-<16 hex>.<pdf|docx>  the original upload, kept so it can be re-parsed

Guarantees:
  * Every write is atomic: written to a temp file in the same directory, fsync'd, then os.replace()d.
    A crash leaves either the old state or the new state, never a torn file.
  * Replacing the CV writes the NEW upload first, then swaps profile.json (which names the upload it
    belongs to), then removes the old upload. A crash in between leaves at most an unreferenced file,
    which the next operation deletes. The profile can never point at a missing or mismatched upload.
  * Writes are compare-and-set on the profile revision, so two writers cannot overwrite each other.
  * Only server-generated file names are ever used; a tampered manifest naming another path is refused.
  * Nothing here logs profile content, filenames or paths.

`SourceCvStore` is the seam for later replacing this with authenticated database storage.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from pydantic import ValidationError

from source_cv.errors import ProfileVersionConflictError, StorageFailureError
from source_cv.extraction import FileKind
from source_cv.schema import SourceProfile

log = logging.getLogger("careergraph.source_cv")

STORE_FORMAT = 1
MANIFEST_NAME = "profile.json"
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
_UPLOAD_NAME = re.compile(r"^upload-[0-9a-f]{16}\.(pdf|docx)$")


@dataclass(frozen=True)
class NewUpload:
    data: bytes
    kind: FileKind


@dataclass(frozen=True)
class StoredUpload:
    data: bytes
    kind: FileKind


class SourceCvStore(Protocol):
    def load(self) -> Optional[SourceProfile]: ...

    def read_upload(self) -> Optional[StoredUpload]: ...

    def commit(
        self, profile: SourceProfile, *, expected_revision: Optional[int], upload: Optional[NewUpload] = None
    ) -> None:
        """Atomically persist `profile` (and, if given, the new original upload, replacing the old one).
        `expected_revision` is the revision currently stored (None when nothing is stored); a mismatch
        raises ProfileVersionConflictError and changes nothing."""

    def delete(self) -> bool: ...


def private_data_dir(environ=None, env_file: Optional[Path] = None) -> Path:
    from llm.config import ROOT_DIR, env_value

    configured = env_value("CAREERGRAPH_PRIVATE_DIR", environ, env_file)
    return Path(configured) if configured else ROOT_DIR / "data" / "private"


class FileSourceCvStore:
    def __init__(self, root: Path):
        self._dir = Path(root) / "source_cv"
        self._lock = threading.RLock()

    # ---- helpers ------------------------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            raise StorageFailureError() from None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temp = path.with_name(f".tmp-{secrets.token_hex(6)}")
        try:
            with open(temp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass  # best effort (Windows ACLs differ)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageFailureError() from None

    def _read_manifest(self) -> Optional[tuple[str, SourceProfile]]:
        path = self._dir / MANIFEST_NAME
        try:
            if not path.exists():
                return None
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise StorageFailureError()
            raw = json.loads(path.read_bytes())
            upload_file = raw["uploadFile"]
            if raw.get("formatVersion") != STORE_FORMAT or not _UPLOAD_NAME.match(upload_file):
                raise StorageFailureError()
            return upload_file, SourceProfile.model_validate(raw["profile"])
        except StorageFailureError:
            raise
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            log.warning("source_cv_store_unreadable")  # no detail: the error text could embed profile content
            raise StorageFailureError() from None

    def _remove_unreferenced(self, keep: Optional[str]) -> None:
        try:
            for entry in self._dir.iterdir():
                if entry.is_file() and entry.name != MANIFEST_NAME and entry.name != keep:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
        except OSError:
            pass

    # ---- SourceCvStore ------------------------------------------------------------------------------

    def load(self) -> Optional[SourceProfile]:
        with self._lock:
            manifest = self._read_manifest()
            return manifest[1] if manifest else None

    def read_upload(self) -> Optional[StoredUpload]:
        with self._lock:
            manifest = self._read_manifest()
            if manifest is None:
                return None
            upload_file, profile = manifest
            try:
                data = (self._dir / upload_file).read_bytes()
            except OSError:
                raise StorageFailureError() from None
            if hashlib.sha256(data).hexdigest() != profile.upload.sha256:
                raise StorageFailureError()
            return StoredUpload(data=data, kind=profile.upload.detectedType)

    def commit(
        self, profile: SourceProfile, *, expected_revision: Optional[int], upload: Optional[NewUpload] = None
    ) -> None:
        with self._lock:
            self._ensure_dir()
            try:
                current = self._read_manifest()
            except StorageFailureError:
                # A damaged store may only be replaced by a fresh upload; edits to it cannot be applied.
                if upload is None:
                    raise
                current = None
                expected_revision = None
            stored_revision = current[1].revision if current else None
            if stored_revision != expected_revision:
                raise ProfileVersionConflictError()

            if upload is not None:
                upload_file = f"upload-{secrets.token_hex(8)}.{upload.kind}"
                self._atomic_write(self._dir / upload_file, upload.data)
            elif current is not None:
                upload_file = current[0]
            else:
                raise StorageFailureError()

            manifest = {
                "formatVersion": STORE_FORMAT,
                "uploadFile": upload_file,
                "profile": json.loads(profile.model_dump_json()),
            }
            try:
                self._atomic_write(self._dir / MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
            except StorageFailureError:
                if upload is not None:
                    try:
                        (self._dir / upload_file).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            self._remove_unreferenced(keep=upload_file)

    def delete(self) -> bool:
        with self._lock:
            if not self._dir.exists():
                return False
            existed = (self._dir / MANIFEST_NAME).exists()
            try:
                manifest_path = self._dir / MANIFEST_NAME
                manifest_path.unlink(missing_ok=True)  # the commit point goes first
            except OSError:
                raise StorageFailureError() from None
            self._remove_unreferenced(keep=None)
            return existed

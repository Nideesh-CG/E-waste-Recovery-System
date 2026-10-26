"""Local filesystem storage abstraction for Circular E-Waste Recovery.
Supports local development and persistent disk storage in production.
No AWS S3 or Supabase dependencies.
"""

import os
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def get_mime_type(filename: str) -> str:
    """Safely derive MIME type from validated file extension."""
    suffix = Path(filename).suffix.lower()
    return MIME_MAP.get(suffix, "application/octet-stream")


def validate_file_content(file_bytes: bytes, filename: str, file_type: str) -> Tuple[bool, str]:
    """Validate file extension, size, and magic bytes signature."""
    if not file_bytes or len(file_bytes) == 0:
        return False, "Uploaded file is empty."
    if len(file_bytes) > MAX_FILE_SIZE:
        return False, "File exceeds maximum size of 5 MB."

    if not filename or "." not in filename:
        return False, f"Missing file extension for {file_type} upload."

    suffix = Path(filename).suffix.lower()
    allowed = ALLOWED_IMAGE_EXTENSIONS if file_type == "image" else ALLOWED_DOC_EXTENSIONS
    if suffix not in allowed:
        return False, f"File extension '{suffix}' is not permitted for {file_type} uploads."

    # Signature validation (magic bytes)
    header = file_bytes[:16]
    is_valid_signature = False

    if suffix in (".jpg", ".jpeg") and (header.startswith(b"\xff\xd8\xff") or header[:2] == b"\xff\xd8"):
        is_valid_signature = True
    elif suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        is_valid_signature = True
    elif suffix == ".gif" and (header.startswith(b"GIF87a") or header.startswith(b"GIF89a")):
        is_valid_signature = True
    elif suffix == ".webp" and len(file_bytes) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        is_valid_signature = True
    elif suffix == ".pdf" and header.startswith(b"%PDF-"):
        is_valid_signature = True

    if not is_valid_signature:
        return False, "File contents do not match the expected format signature."

    return True, ""


class BaseStorage(ABC):
    """Abstract storage interface."""

    @abstractmethod
    def save(self, file_bytes: bytes, filename: str, content_type: Optional[str] = None, is_private: bool = True) -> Tuple[str, str]:
        """Save file bytes and return (storage_key, access_url_or_locator)."""
        pass

    @abstractmethod
    def retrieve(self, storage_key: str) -> Tuple[bytes, str]:
        """Retrieve file bytes and content type."""
        pass

    @abstractmethod
    def delete(self, storage_key: str) -> bool:
        """Delete file by storage key."""
        pass

    @abstractmethod
    def get_url(self, storage_key: str, is_private: bool = True) -> str:
        """Get accessible URL path."""
        pass


class LocalStorage(BaseStorage):
    """Local and persistent disk filesystem storage."""

    def __init__(self, upload_folder: str):
        self.upload_folder = Path(upload_folder).resolve()
        self.upload_folder.mkdir(parents=True, exist_ok=True)
        # Verify write permissions to ensure early, safe failure if storage is not writable
        probe_key = f".write_probe_{secrets.token_hex(4)}"
        probe_path = self.upload_folder / probe_key
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
        except OSError as exc:
            raise PermissionError(f"Upload storage directory '{self.upload_folder}' is not writable: {exc}")

    def save(self, file_bytes: bytes, filename: str, content_type: Optional[str] = None, is_private: bool = True) -> Tuple[str, str]:
        suffix = Path(filename).suffix.lower()
        storage_key = f"{secrets.token_hex(16)}{suffix}"
        target_path = self.upload_folder / storage_key
        target_path.write_bytes(file_bytes)

        # For public images: /uploads/<key>, for private documents: /api/v1/files/<key>
        url = f"/uploads/{storage_key}" if not is_private else f"/api/v1/files/{storage_key}"
        return storage_key, url

    def retrieve(self, storage_key: str) -> Tuple[bytes, str]:
        safe_key = Path(storage_key).name
        target_path = (self.upload_folder / safe_key).resolve()

        # Prevent path traversal
        if not str(target_path).startswith(str(self.upload_folder)):
            raise ValueError("Path traversal detected.")

        if not target_path.is_file():
            raise FileNotFoundError(f"File {storage_key} not found.")

        suffix = target_path.suffix.lower()
        content_type = MIME_MAP.get(suffix, "application/octet-stream")
        return target_path.read_bytes(), content_type

    def delete(self, storage_key: str) -> bool:
        safe_key = Path(storage_key).name
        target_path = (self.upload_folder / safe_key).resolve()

        if not str(target_path).startswith(str(self.upload_folder)):
            return False

        if target_path.is_file():
            target_path.unlink()
            return True
        return False

    def get_url(self, storage_key: str, is_private: bool = True) -> str:
        return f"/api/v1/files/{storage_key}" if is_private else f"/uploads/{storage_key}"


def get_storage_service(config) -> BaseStorage:
    """Factory to instantiate local filesystem storage service."""
    folder = None
    if hasattr(config, "get"):
        folder = config.get("UPLOAD_STORAGE_PATH") or config.get("UPLOAD_FOLDER")
    if not folder:
        folder = getattr(config, "UPLOAD_STORAGE_PATH", None) or getattr(config, "UPLOAD_FOLDER", None)
    if not folder:
        base_dir = Path(__file__).resolve().parent
        folder = str(base_dir / "instance" / "uploads")
    return LocalStorage(folder)


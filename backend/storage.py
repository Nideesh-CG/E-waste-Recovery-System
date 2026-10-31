"""Storage abstraction for Circular E-Waste Recovery Platform.
Supports local filesystem storage for development/testing and Supabase Storage for production.
"""

import io
import os
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple
import requests

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
    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
        is_private: bool = True,
        device_id: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> Tuple[str, str]:
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
    """Local and persistent disk filesystem storage for development and offline testing."""

    def __init__(self, upload_folder: str):
        self.upload_folder = Path(upload_folder).resolve()
        self.upload_folder.mkdir(parents=True, exist_ok=True)
        probe_key = f".write_probe_{secrets.token_hex(4)}"
        probe_path = self.upload_folder / probe_key
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
        except OSError as exc:
            raise PermissionError(f"Upload storage directory '{self.upload_folder}' is not writable: {exc}")

    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
        is_private: bool = True,
        device_id: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> Tuple[str, str]:
        suffix = Path(filename).suffix.lower()
        storage_key = f"{secrets.token_hex(16)}{suffix}"
        target_path = self.upload_folder / storage_key
        target_path.write_bytes(file_bytes)
        url = f"/api/v1/files/{storage_key}" if is_private else f"/uploads/{storage_key}"
        return storage_key, url

    def retrieve(self, storage_key: str) -> Tuple[bytes, str]:
        normalized_key = storage_key.lstrip("/\\")
        target_path = (self.upload_folder / normalized_key).resolve()

        if not str(target_path).startswith(str(self.upload_folder)):
            raise ValueError("Path traversal detected.")

        if not target_path.is_file():
            # Check flat basename fallback for legacy keys
            flat_path = (self.upload_folder / Path(storage_key).name).resolve()
            if flat_path.is_file() and str(flat_path).startswith(str(self.upload_folder)):
                target_path = flat_path
            else:
                raise FileNotFoundError(f"File {storage_key} not found.")

        suffix = target_path.suffix.lower()
        content_type = MIME_MAP.get(suffix, "application/octet-stream")
        return target_path.read_bytes(), content_type

    def delete(self, storage_key: str) -> bool:
        normalized_key = storage_key.lstrip("/\\")
        target_path = (self.upload_folder / normalized_key).resolve()
        if not str(target_path).startswith(str(self.upload_folder)):
            return False
        if target_path.is_file():
            target_path.unlink()
            return True
        flat_path = (self.upload_folder / Path(storage_key).name).resolve()
        if flat_path.is_file() and str(flat_path).startswith(str(self.upload_folder)):
            flat_path.unlink()
            return True
        return False

    def get_url(self, storage_key: str, is_private: bool = True) -> str:
        return f"/api/v1/files/{storage_key}" if is_private else f"/uploads/{storage_key}"


class SupabaseStorage(BaseStorage):
    """Supabase Private Object Storage abstraction using REST API.
    
    Organizes files into:
        collection/devices/{device_id}/images/{token}_{filename}
        collection/devices/{device_id}/documents/{token}_{filename}
    """

    def __init__(self, supabase_url: str, supabase_key: str, bucket: str = "ewaste-files"):
        if not supabase_url:
            raise ValueError("SupabaseStorage requires a valid supabase_url.")
        if not supabase_key:
            raise ValueError("SupabaseStorage requires a valid supabase_key.")
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key.strip()
        self.bucket = (bucket or "ewaste-files").strip()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.supabase_key}",
            "apikey": self.supabase_key,
        })
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Verify or create private bucket if missing (fail-safe)."""
        try:
            res = self.session.get(f"{self.supabase_url}/storage/v1/bucket/{self.bucket}", timeout=5)
            if res.status_code == 404:
                self.session.post(
                    f"{self.supabase_url}/storage/v1/bucket",
                    json={"id": self.bucket, "name": self.bucket, "public": False},
                    timeout=5,
                )
        except Exception:
            pass

    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
        is_private: bool = True,
        device_id: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> Tuple[str, str]:
        if not content_type:
            content_type = get_mime_type(filename)

        token = secrets.token_hex(8)
        safe_filename = Path(filename).name
        dev_id = device_id or "unassigned"
        file_folder = "images" if file_type == "image" else "documents"
        storage_key = f"collection/devices/{dev_id}/{file_folder}/{token}_{safe_filename}"

        upload_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}/{storage_key}"
        headers = {
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        resp = self.session.post(upload_url, data=file_bytes, headers=headers, timeout=15)
        if resp.status_code not in (200, 201):
            raise IOError(f"Supabase Storage upload failed with status {resp.status_code}: {resp.text}")

        url = f"/api/v1/files/{storage_key}"
        return storage_key, url

    def retrieve(self, storage_key: str) -> Tuple[bytes, str]:
        download_url = f"{self.supabase_url}/storage/v1/object/authenticated/{self.bucket}/{storage_key}"
        resp = self.session.get(download_url, timeout=15)
        if resp.status_code == 404:
            raise FileNotFoundError(f"File '{storage_key}' not found in Supabase Storage.")
        if resp.status_code in (401, 403):
            raise PermissionError(f"Access denied to Supabase Storage object '{storage_key}'.")
        if resp.status_code != 200:
            raise IOError(f"Supabase Storage download failed with status {resp.status_code}: {resp.text}")

        content_type = resp.headers.get("Content-Type") or get_mime_type(storage_key)
        return resp.content, content_type

    def delete(self, storage_key: str) -> bool:
        delete_url = f"{self.supabase_url}/storage/v1/object/{self.bucket}"
        resp = self.session.delete(delete_url, json={"prefixes": [storage_key]}, timeout=10)
        return resp.status_code == 200

    def create_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        sign_url = f"{self.supabase_url}/storage/v1/object/sign/{self.bucket}/{storage_key}"
        resp = self.session.post(sign_url, json={"expiresIn": expires_in}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            signed_path = data.get("signedURL")
            if signed_path:
                if signed_path.startswith("http"):
                    return signed_path
                return f"{self.supabase_url}/storage/v1{signed_path}"
        raise IOError(f"Failed to generate signed URL: {resp.text}")

    def get_url(self, storage_key: str, is_private: bool = True) -> str:
        return f"/api/v1/files/{storage_key}"


def get_storage_service(config=None) -> BaseStorage:
    """Factory to instantiate storage service (Supabase Storage in production or LocalStorage)."""
    cfg = config or {}

    def get_val(key, default=""):
        if hasattr(cfg, "get"):
            v = cfg.get(key)
            if v is not None:
                return v
        return getattr(cfg, key, None) or os.environ.get(key, default)

    storage_provider = str(get_val("STORAGE_PROVIDER", "")).strip().lower()
    supabase_url = get_val("SUPABASE_URL", "").strip()
    supabase_key = (
        get_val("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or get_val("SUPABASE_KEY", "").strip()
    )
    bucket = get_val("SUPABASE_STORAGE_BUCKET", "ewaste-files").strip() or "ewaste-files"

    is_prod = (
        str(get_val("IS_PRODUCTION", "")).lower() in ("true", "1", "yes")
        or bool(os.environ.get("VERCEL"))
        or bool(os.environ.get("RENDER"))
    )

    if storage_provider == "supabase" or (is_prod and supabase_url and supabase_key):
        return SupabaseStorage(supabase_url=supabase_url, supabase_key=supabase_key, bucket=bucket)

    if is_prod:
        raise ValueError(
            "In production, storage provider must be 'supabase' with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY configured. Local filesystem storage fallback is disallowed."
        )

    folder = get_val("UPLOAD_STORAGE_PATH") or get_val("UPLOAD_FOLDER")
    if not folder:
        base_dir = Path(__file__).resolve().parent
        folder = str(base_dir / "instance" / "uploads")
    return LocalStorage(folder)

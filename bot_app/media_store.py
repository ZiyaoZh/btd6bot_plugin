from __future__ import annotations

import hashlib
import io
import ipaddress
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError

from .protocol import ImagePayload


class MediaStoreError(ValueError):
    """A source image could not be safely cached."""


@dataclass(frozen=True)
class CachedImage:
    segment_index: int
    status: str
    mime_type: str = ""
    source_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    sha256: str = ""
    cache_path: str = ""
    expires_at: str = ""
    error: str = ""
    cache_hit: bool = False
    normalized_bytes: int = 0


class ChatMediaStore:
    """Bounded, content-addressed cache for untrusted chat images."""

    def __init__(
        self,
        root: str | Path,
        *,
        timeout_seconds: float = 10.0,
        max_image_bytes: int = 10 * 1024 * 1024,
        max_pixels: int = 20_000_000,
        max_dimension: int = 2048,
        ttl_seconds: float = 6 * 3600,
        capacity_bytes: int = 512 * 1024 * 1024,
        session: requests.Session | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_image_bytes = max(1024, int(max_image_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self.max_dimension = max(64, int(max_dimension))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.capacity_bytes = max(self.max_image_bytes, int(capacity_bytes))
        self._session = session or requests.Session()

    def cache(self, image: ImagePayload) -> CachedImage:
        segment_index = int(image.get("segment_index", 0))
        source_url = str(image.get("url", "") or "").strip()
        if not source_url:
            return CachedImage(segment_index, "unavailable", error="missing source URL")
        declared_size = image.get("file_size")
        if declared_size is not None and int(declared_size) > self.max_image_bytes:
            return CachedImage(
                segment_index, "rejected", error="image exceeds byte limit"
            )

        try:
            source = self._download(source_url)
            normalized, mime_type, width, height, suffix = self._normalize(source)
            digest = hashlib.sha256(normalized).hexdigest()
            target = self.root / digest[:2] / f"{digest}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            cache_hit = target.exists()
            if not cache_hit:
                self._atomic_write(target, normalized)
            os.utime(target, None)
            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.ttl_seconds
            )
            self.cleanup()
            return CachedImage(
                segment_index=segment_index,
                status="cached",
                mime_type=mime_type,
                source_bytes=len(source),
                width=width,
                height=height,
                sha256=digest,
                cache_path=str(target.resolve()),
                expires_at=expires_at.isoformat(),
                cache_hit=cache_hit,
                normalized_bytes=len(normalized),
            )
        except MediaStoreError as exc:
            return CachedImage(segment_index, "rejected", error=str(exc))

    def cleanup(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        files = [path for path in self.root.rglob("*") if path.is_file()]
        retained: list[tuple[Path, os.stat_result]] = []
        for path in files:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if now - stat.st_mtime >= self.ttl_seconds:
                path.unlink(missing_ok=True)
            else:
                retained.append((path, stat))

        total = sum(stat.st_size for _, stat in retained)
        for path, stat in sorted(retained, key=lambda item: item[1].st_mtime):
            if total <= self.capacity_bytes:
                break
            path.unlink(missing_ok=True)
            total -= stat.st_size
        self._remove_empty_directories()

    def _download(self, url: str) -> bytes:
        self._validate_url(url)
        try:
            response = self._session.get(
                url,
                stream=True,
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MediaStoreError(f"download failed: {type(exc).__name__}") from exc

        for followed in [*response.history, response]:
            self._validate_url(str(followed.url))
        content_length = response.headers.get("Content-Length", "")
        if content_length:
            try:
                if int(content_length) > self.max_image_bytes:
                    raise MediaStoreError("image exceeds byte limit")
            except ValueError:
                pass

        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > self.max_image_bytes:
                    raise MediaStoreError("image exceeds byte limit")
        finally:
            response.close()
        if not body:
            raise MediaStoreError("empty image response")
        return bytes(body)

    def _normalize(self, source: bytes) -> tuple[bytes, str, int, int, str]:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = self.max_pixels
        try:
            with Image.open(io.BytesIO(source)) as opened:
                width, height = opened.size
                if width * height > self.max_pixels:
                    raise MediaStoreError("image exceeds pixel limit")
                opened.seek(0)
                opened.load()
                image = opened.copy()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise MediaStoreError("invalid or unsafe image") from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit

        image.thumbnail(
            (self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS
        )
        output = io.BytesIO()
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            normalized = image.convert("RGBA")
            normalized.save(output, format="PNG", optimize=True)
            mime_type, suffix = "image/png", ".png"
        else:
            normalized = image.convert("RGB")
            normalized.save(output, format="JPEG", quality=88, optimize=True)
            mime_type, suffix = "image/jpeg", ".jpg"
        return output.getvalue(), mime_type, normalized.width, normalized.height, suffix

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaStoreError("only HTTP(S) image URLs are allowed")
        if parsed.username or parsed.password:
            raise MediaStoreError("credentialed image URLs are not allowed")
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                )
            }
        except socket.gaierror as exc:
            raise MediaStoreError("image host cannot be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise MediaStoreError("non-public image hosts are not allowed")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        handle, temporary_name = tempfile.mkstemp(prefix=".media-", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(content)
            os.replace(temporary_name, path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    def _remove_empty_directories(self) -> None:
        for directory in sorted(self.root.rglob("*"), reverse=True):
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass

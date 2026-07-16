from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Sequence
from uuid import uuid4


ACTIVE_STATUS = "active"
ARCHIVED_STATUS = "archived"


@dataclass(frozen=True)
class ArtifactSource:
    path: Path
    role: str
    media_type: str = ""
    remove_after_store: bool = False


@dataclass(frozen=True)
class ArtifactFile:
    role: str
    path: Path
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    title: str
    summary: str
    tags: tuple[str, ...]
    status: str
    created_at: datetime
    expires_at: datetime
    last_accessed_at: datetime
    hit_count: int
    archived_at: datetime | None
    delete_after: datetime | None
    metadata: dict[str, Any]
    files: tuple[ArtifactFile, ...]

    def file(self, role: str) -> Path | None:
        normalized_role = _normalize_role(role)
        for item in self.files:
            if item.role == normalized_role:
                return item.path
        return None


@dataclass(frozen=True)
class MaintenanceResult:
    archived: int = 0
    deleted: int = 0
    staging_deleted: int = 0


class ArtifactStore:
    """Persistent tag index and lifecycle manager for agent-produced files."""

    def __init__(
        self,
        root: Path,
        *,
        default_ttl_seconds: float = 86_400,
        archive_retention_seconds: float = 7 * 86_400,
        maintenance_interval_seconds: float = 300,
        staging_retention_seconds: float = 86_400,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.active_dir = self.root / "active"
        self.archive_dir = self.root / "archive"
        self.staging_dir = self.root / ".staging"
        self.default_ttl_seconds = max(1.0, float(default_ttl_seconds))
        self.archive_retention_seconds = max(
            0.0, float(archive_retention_seconds)
        )
        self.maintenance_interval_seconds = max(
            0.0, float(maintenance_interval_seconds)
        )
        self.staging_retention_seconds = max(60.0, float(staging_retention_seconds))
        self._lock = RLock()
        self._last_maintenance_at: datetime | None = None

        for directory in (
            self.root,
            self.active_dir,
            self.archive_dir,
            self.staging_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.root / "index.db",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self.maintain(force=True)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def list_active(
        self,
        *,
        limit: int = 50,
        kind: str = "",
        now: datetime | None = None,
    ) -> tuple[ArtifactRecord, ...]:
        current = _as_utc(now)
        self._maybe_maintain(current)
        normalized_kind = str(kind or "").strip().lower()
        fetch_limit = max(1, min(200, int(limit)))

        with self._lock:
            if normalized_kind:
                rows = self._conn.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE status = ? AND expires_at > ? AND kind = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (
                        ACTIVE_STATUS,
                        _format_datetime(current),
                        normalized_kind,
                        fetch_limit,
                    ),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE status = ? AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (ACTIVE_STATUS, _format_datetime(current), fetch_limit),
                ).fetchall()

            records: list[ArtifactRecord] = []
            for row in rows:
                record = self._hydrate_record_locked(row)
                if not self._record_files_exist(record):
                    artifact_dir = self._artifact_directory(record)
                    if artifact_dir is not None:
                        shutil.rmtree(artifact_dir, ignore_errors=True)
                    self._delete_record_locked(record.artifact_id)
                    continue
                records.append(record)
            return tuple(records)

    def get_active(
        self,
        artifact_id: str,
        *,
        now: datetime | None = None,
        touch: bool = True,
    ) -> ArtifactRecord | None:
        current = _as_utc(now)
        normalized_id = str(artifact_id or "").strip().lower()
        if not normalized_id:
            return None
        self._maybe_maintain(current)

        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM artifacts
                WHERE artifact_id = ? AND status = ? AND expires_at > ?
                """,
                (normalized_id, ACTIVE_STATUS, _format_datetime(current)),
            ).fetchone()
            if row is None:
                return None
            record = self._hydrate_record_locked(row)
            if not self._record_files_exist(record):
                artifact_dir = self._artifact_directory(record)
                if artifact_dir is not None:
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                self._delete_record_locked(record.artifact_id)
                return None
            if not touch:
                return record
            self._conn.execute(
                """
                UPDATE artifacts
                SET last_accessed_at = ?, hit_count = hit_count + 1
                WHERE artifact_id = ?
                """,
                (_format_datetime(current), normalized_id),
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (normalized_id,),
            ).fetchone()
            return self._hydrate_record_locked(updated)

    def register(
        self,
        *,
        kind: str,
        title: str,
        summary: str = "",
        tags: Iterable[str],
        files: Sequence[ArtifactSource],
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        remove_sources: bool = False,
        now: datetime | None = None,
    ) -> ArtifactRecord:
        current = _as_utc(now)
        normalized_kind = _normalize_kind(kind)
        normalized_title = str(title or "").strip() or normalized_kind
        normalized_summary = str(summary or "").strip()
        normalized_tags = _normalize_tags(tags)
        normalized_sources = self._normalize_sources(files)
        if not normalized_sources:
            raise ValueError("artifact must contain at least one file")
        self._maybe_maintain(current)

        artifact_id = uuid4().hex
        kind_dir = _safe_component(normalized_kind)
        temporary_dir = self.staging_dir / f"register-{artifact_id}"
        destination_dir = self.active_dir / kind_dir / artifact_id
        temporary_dir.mkdir(parents=True, exist_ok=False)

        file_rows: list[tuple[str, str, str, int, str]] = []
        try:
            for source in normalized_sources:
                suffix = source.path.suffix.lower()
                destination_name = f"{_safe_component(source.role)}{suffix}"
                destination = temporary_dir / destination_name
                shutil.copy2(source.path, destination)
                size_bytes, digest = _file_fingerprint(destination)
                media_type = source.media_type or (
                    mimetypes.guess_type(destination.name)[0]
                    or "application/octet-stream"
                )
                relative_path = str(
                    (destination_dir / destination_name).relative_to(self.root)
                )
                file_rows.append(
                    (
                        source.role,
                        relative_path,
                        media_type,
                        size_bytes,
                        digest,
                    )
                )

            destination_dir.parent.mkdir(parents=True, exist_ok=True)
            temporary_dir.replace(destination_dir)

            lifetime = (
                self.default_ttl_seconds
                if ttl_seconds is None
                else max(1.0, float(ttl_seconds))
            )
            expires_at = current + timedelta(seconds=lifetime)
            serialized_metadata = json.dumps(
                metadata or {},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    self._conn.execute(
                        """
                        INSERT INTO artifacts (
                            artifact_id, kind, title, summary, status,
                            created_at, expires_at,
                            last_accessed_at, hit_count, archived_at,
                            delete_after, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?)
                        """,
                        (
                            artifact_id,
                            normalized_kind,
                            normalized_title,
                            normalized_summary,
                            ACTIVE_STATUS,
                            _format_datetime(current),
                            _format_datetime(expires_at),
                            _format_datetime(current),
                            serialized_metadata,
                        ),
                    )
                    self._conn.executemany(
                        "INSERT INTO artifact_tags (artifact_id, tag) VALUES (?, ?)",
                        [(artifact_id, tag) for tag in normalized_tags],
                    )
                    self._conn.executemany(
                        """
                        INSERT INTO artifact_files (
                            artifact_id, role, relative_path, media_type,
                            size_bytes, sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [(artifact_id, *row) for row in file_rows],
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise

                row = self._conn.execute(
                    "SELECT * FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
                record = self._hydrate_record_locked(row)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            shutil.rmtree(destination_dir, ignore_errors=True)
            raise

        for source in normalized_sources:
            if remove_sources or source.remove_after_store:
                source.path.unlink(missing_ok=True)
        return record

    def maintain(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> MaintenanceResult:
        current = _as_utc(now)
        with self._lock:
            if not force and not self._maintenance_due(current):
                return MaintenanceResult()

            archived = 0
            deleted = 0
            expired_rows = self._conn.execute(
                """
                SELECT * FROM artifacts
                WHERE status = ? AND expires_at <= ?
                ORDER BY expires_at ASC
                """,
                (ACTIVE_STATUS, _format_datetime(current)),
            ).fetchall()
            for row in expired_rows:
                self._archive_record_locked(row, current)
                archived += 1

            delete_rows = self._conn.execute(
                """
                SELECT * FROM artifacts
                WHERE status = ? AND delete_after <= ?
                ORDER BY delete_after ASC
                """,
                (ARCHIVED_STATUS, _format_datetime(current)),
            ).fetchall()
            for row in delete_rows:
                record = self._hydrate_record_locked(row)
                artifact_dir = self._artifact_directory(record)
                if artifact_dir is not None:
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                self._delete_record_locked(record.artifact_id, commit=False)
                deleted += 1
            self._conn.commit()

            staging_deleted = self._cleanup_staging(current)
            self._last_maintenance_at = current
            return MaintenanceResult(
                archived=archived,
                deleted=deleted,
                staging_deleted=staging_deleted,
            )

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT,
                    delete_after TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS artifact_tags (
                    artifact_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, tag),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifact_files (
                    artifact_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, role),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_lifecycle
                ON artifacts(status, expires_at, delete_after);

                CREATE INDEX IF NOT EXISTS idx_artifacts_kind_active
                ON artifacts(kind, status, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_artifact_tags_lookup
                ON artifact_tags(tag, artifact_id);
                """
            )
            self._ensure_column(
                "artifacts",
                "title",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                "artifacts",
                "summary",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def _normalize_sources(
        self, files: Sequence[ArtifactSource]
    ) -> list[ArtifactSource]:
        normalized: list[ArtifactSource] = []
        roles: set[str] = set()
        for item in files:
            path = item.path.expanduser().resolve()
            role = _normalize_role(item.role)
            if role in roles:
                raise ValueError(f"duplicate artifact file role: {role}")
            if not path.is_file():
                raise FileNotFoundError(path)
            roles.add(role)
            normalized.append(
                ArtifactSource(
                    path=path,
                    role=role,
                    media_type=str(item.media_type or "").strip().lower(),
                    remove_after_store=bool(item.remove_after_store),
                )
            )
        return normalized

    def _hydrate_record_locked(self, row: sqlite3.Row | None) -> ArtifactRecord:
        if row is None:
            raise LookupError("artifact index row does not exist")
        artifact_id = str(row["artifact_id"])
        tag_rows = self._conn.execute(
            "SELECT tag FROM artifact_tags WHERE artifact_id = ? ORDER BY tag",
            (artifact_id,),
        ).fetchall()
        file_rows = self._conn.execute(
            """
            SELECT role, relative_path, media_type, size_bytes, sha256
            FROM artifact_files
            WHERE artifact_id = ?
            ORDER BY role
            """,
            (artifact_id,),
        ).fetchall()
        files = tuple(
            ArtifactFile(
                role=str(item["role"]),
                path=self._resolve_indexed_path(str(item["relative_path"])),
                media_type=str(item["media_type"]),
                size_bytes=int(item["size_bytes"]),
                sha256=str(item["sha256"]),
            )
            for item in file_rows
        )
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return ArtifactRecord(
            artifact_id=artifact_id,
            kind=str(row["kind"]),
            title=str(row["title"] or row["kind"]),
            summary=str(row["summary"] or ""),
            tags=tuple(str(item["tag"]) for item in tag_rows),
            status=str(row["status"]),
            created_at=_parse_datetime(str(row["created_at"])),
            expires_at=_parse_datetime(str(row["expires_at"])),
            last_accessed_at=_parse_datetime(str(row["last_accessed_at"])),
            hit_count=int(row["hit_count"]),
            archived_at=_parse_optional_datetime(row["archived_at"]),
            delete_after=_parse_optional_datetime(row["delete_after"]),
            metadata=metadata,
            files=files,
        )

    def _archive_record_locked(
        self, row: sqlite3.Row, current: datetime
    ) -> None:
        record = self._hydrate_record_locked(row)
        source_dir = self._artifact_directory(record)
        kind_dir = _safe_component(record.kind)
        destination_dir = self.archive_dir / kind_dir / record.artifact_id
        if source_dir is not None and source_dir.exists():
            destination_dir.parent.mkdir(parents=True, exist_ok=True)
            if destination_dir.exists():
                shutil.rmtree(destination_dir)
            source_dir.replace(destination_dir)

        delete_after = current + timedelta(seconds=self.archive_retention_seconds)
        for item in record.files:
            archived_path = destination_dir / item.path.name
            self._conn.execute(
                """
                UPDATE artifact_files SET relative_path = ?
                WHERE artifact_id = ? AND role = ?
                """,
                (
                    str(archived_path.relative_to(self.root)),
                    record.artifact_id,
                    item.role,
                ),
            )
        self._conn.execute(
            """
            UPDATE artifacts
            SET status = ?, archived_at = ?, delete_after = ?
            WHERE artifact_id = ?
            """,
            (
                ARCHIVED_STATUS,
                _format_datetime(current),
                _format_datetime(delete_after),
                record.artifact_id,
            ),
        )

    def _artifact_directory(self, record: ArtifactRecord) -> Path | None:
        if not record.files:
            return None
        candidate = record.files[0].path.parent.resolve()
        self._ensure_under_root(candidate)
        return candidate

    def _record_files_exist(self, record: ArtifactRecord) -> bool:
        return bool(record.files) and all(item.path.is_file() for item in record.files)

    def _delete_record_locked(
        self, artifact_id: str, *, commit: bool = True
    ) -> None:
        self._conn.execute(
            "DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        )
        if commit:
            self._conn.commit()

    def _resolve_indexed_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        self._ensure_under_root(candidate)
        return candidate

    def _ensure_under_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact path escapes artifact root") from exc

    def _maybe_maintain(self, current: datetime) -> None:
        if self._maintenance_due(current):
            self.maintain(now=current)

    def _maintenance_due(self, current: datetime) -> bool:
        if self._last_maintenance_at is None:
            return True
        elapsed = (current - self._last_maintenance_at).total_seconds()
        return elapsed >= self.maintenance_interval_seconds

    def _cleanup_staging(self, current: datetime) -> int:
        deleted = 0
        cutoff = current.timestamp() - self.staging_retention_seconds
        for path in self.staging_dir.iterdir():
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                deleted += 1
            except FileNotFoundError:
                continue
        return deleted


def _normalize_kind(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("artifact kind cannot be empty")
    return normalized


def _normalize_role(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("artifact file role cannot be empty")
    return normalized


def _normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    normalized = {
        str(tag or "").strip().lower() for tag in tags if str(tag or "").strip()
    }
    return tuple(sorted(normalized))


def _safe_component(value: str) -> str:
    normalized = "".join(
        char if char.isascii() and (char.isalnum() or char in {"-", "_"}) else "_"
        for char in str(value or "").strip().lower()
    ).strip("_")
    return normalized or "artifact"


def _file_fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _parse_optional_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    return _parse_datetime(text) if text else None

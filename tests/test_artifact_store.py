from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot_app.artifact_store import ArtifactSource, ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def test_catalog_entry_archives_then_deletes_expired_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.md"
            source.write_text("# 活动报告\n测试内容", encoding="utf-8")
            store = ArtifactStore(
                root / "artifacts",
                default_ttl_seconds=60,
                archive_retention_seconds=120,
                maintenance_interval_seconds=0,
            )
            base = datetime.now(timezone.utc)

            record = store.register(
                kind="activity.detail",
                title="BTD6 竞速活动详情",
                summary="竞速活动测试内容",
                tags=(
                    "BTD6",
                    "活动详情",
                    "竞速",
                ),
                files=[
                    ArtifactSource(
                        path=source,
                        role="report",
                        remove_after_store=True,
                    )
                ],
                ttl_seconds=60,
                now=base,
            )

            self.assertFalse(source.exists())
            self.assertEqual(record.status, "active")
            self.assertEqual(record.title, "BTD6 竞速活动详情")
            self.assertEqual(record.summary, "竞速活动测试内容")
            self.assertEqual(record.file("report").read_text(encoding="utf-8"), "# 活动报告\n测试内容")  # type: ignore[union-attr]
            self.assertIn("active", record.file("report").parts)  # type: ignore[union-attr]

            catalog = store.list_active(
                now=base + timedelta(seconds=30),
            )
            self.assertEqual([item.artifact_id for item in catalog], [record.artifact_id])
            hit = store.get_active(
                record.artifact_id,
                now=base + timedelta(seconds=30),
            )
            self.assertIsNotNone(hit)
            self.assertEqual(hit.hit_count, 1)  # type: ignore[union-attr]

            archived = store.maintain(
                now=base + timedelta(seconds=61),
                force=True,
            )
            archived_path = (
                store.archive_dir
                / "activity_detail"
                / record.artifact_id
                / "report.md"
            )
            self.assertEqual(archived.archived, 1)
            self.assertTrue(archived_path.is_file())
            self.assertFalse(record.file("report").exists())  # type: ignore[union-attr]
            self.assertIsNone(
                store.get_active(
                    record.artifact_id,
                    now=base + timedelta(seconds=62),
                )
            )

            with sqlite3.connect(store.root / "index.db") as connection:
                status = connection.execute(
                    "SELECT status FROM artifacts WHERE artifact_id = ?",
                    (record.artifact_id,),
                ).fetchone()
                tags = connection.execute(
                    "SELECT tag FROM artifact_tags WHERE artifact_id = ? ORDER BY tag",
                    (record.artifact_id,),
                ).fetchall()
            self.assertEqual(status, ("archived",))
            self.assertEqual(
                [item[0] for item in tags],
                [
                    "btd6",
                    "活动详情",
                    "竞速",
                ],
            )

            deleted = store.maintain(
                now=base + timedelta(seconds=182),
                force=True,
            )
            self.assertEqual(deleted.deleted, 1)
            self.assertFalse(archived_path.exists())
            with sqlite3.connect(store.root / "index.db") as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM artifacts"
                ).fetchone()[0]
            self.assertEqual(count, 0)
            store.close()


if __name__ == "__main__":
    unittest.main()

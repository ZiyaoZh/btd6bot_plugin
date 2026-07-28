from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = PLUGIN_ROOT / "btd6_knowledge_base"
sys.path.insert(0, str(KB_ROOT))

from crawler import WikiSpider  # noqa: E402
from llm_cleaner import (  # noqa: E402
    LLMCleanerConfig,
    LLMCleaningError,
    LLMKnowledgeCleaner,
)
from markdown_store import extract_sections_for_file, search_file_candidates  # noqa: E402
from wiki_to_markdown import (  # noqa: E402
    normalize_structural_spacing,
    reconcile_interrupted_documents,
    transform_dataset,
)


PAGE_A = """<!doctype html>
<html><head><title>忍者猴 - BTD6 Wiki</title></head><body>
<h1 id="firstHeading">忍者猴</h1>
<div id="mw-content-text"><div class="mw-parser-output">
  <div class="bread-edit">编 刷 历</div>
  <p><a href="/btd6/%E9%A6%96%E9%A1%B5">首页</a> &gt; 忍者猴</p>
  <h2><span id="简介">简介</span></h2>
  <p>忍者猴可以攻击迷彩气球，基础伤害为 1。</p>
  <h3><span id="路线3">路线3</span></h3>
  <p>下路升级会放置路障，并参考<a href="/btd6/%E9%87%8E%E5%85%BD%E4%B9%8B%E4%B8%BB#DDT">野兽之主</a>机制。</p>
  <table><tr><th>等级</th><th>价格</th></tr><tr><td>1</td><td>250</td></tr></table>
</div></div></body></html>"""

PAGE_B = """<!doctype html>
<html><head><title>野兽之主</title></head><body>
<h1 id="firstHeading">野兽之主</h1>
<div id="mw-content-text"><div class="mw-parser-output">
  <h2><span id="DDT">DDT</span></h2>
  <p>逆戟鲸可以拖拽部分飞艇。</p>
</div></div></body></html>"""


class MarkdownKnowledgeBaseTests(unittest.TestCase):
    def _write_source(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "忍者猴.html").write_text(PAGE_A, encoding="utf-8")
        (root / "%E9%87%8E%E5%85%BD%E4%B9%8B%E4%B8%BB.html").write_text(
            PAGE_B, encoding="utf-8"
        )

    def test_conversion_preserves_structure_and_local_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "raw"
            output = root / "docs"
            self._write_source(source)

            result = transform_dataset(source, output)

            self.assertEqual(result["converted"], 2)
            markdown = (output / "忍者猴.md").read_text(encoding="utf-8")
            self.assertIn("## 目录", markdown)
            self.assertIn("### 路线3", markdown)
            self.assertIn("[野兽之主](./野兽之主.md#ddt)", markdown)
            self.assertIn("| 等级 | 价格 |", markdown)
            self.assertIn("## 相关文档", markdown)
            self.assertNotIn("编 刷 历", markdown)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["documents"]["忍者猴.md"]["document_version"], 1)

    def test_incremental_update_skips_unchanged_and_archives_previous_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "raw"
            output = root / "docs"
            self._write_source(source)
            first = transform_dataset(source, output)
            second = transform_dataset(source, output)
            self.assertEqual(first["converted"], 2)
            self.assertEqual(second["converted"], 0)
            self.assertEqual(second["skipped_unchanged"], 2)

            (source / "忍者猴.html").write_text(
                PAGE_A.replace("基础伤害为 1", "基础伤害为 2"), encoding="utf-8"
            )
            third = transform_dataset(source, output)
            self.assertEqual(third["converted"], 1)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            record = manifest["documents"]["忍者猴.md"]
            self.assertEqual(record["document_version"], 2)
            archive = output / record["history"][0]["archive_path"]
            self.assertTrue(archive.is_file())
            self.assertIn("基础伤害为 1", archive.read_text(encoding="utf-8"))

    def test_direct_search_and_section_extraction_need_no_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "raw"
            output = root / "docs"
            self._write_source(source)
            transform_dataset(source, output)

            candidates = search_file_candidates(
                corpus_dir=output,
                query="忍者猴 下路",
                top_k=2,
                entity_terms=["忍者猴"],
                mechanic_terms=["下路"],
            )
            self.assertEqual(Path(candidates[0]["path"]).name, "忍者猴.md")
            extracted = extract_sections_for_file(
                file_name="忍者猴.md",
                query="路线3 路障",
                corpus_dir=output,
                alias_file=None,
                max_chars=1000,
            )
            self.assertIn("### 路线3", extracted["text"])
            self.assertIn("路障", extracted["text"])
            self.assertNotIn("## 目录", extracted["text"])
            self.assertEqual(extracted["mode"], "markdown_sections")

    def test_llm_markdown_cleaner_rejects_reference_or_fact_changes(self) -> None:
        cleaner = LLMKnowledgeCleaner(
            LLMCleanerConfig(
                api_key="test",
                api_base_url="https://example.invalid/v1",
                model="test-model",
                max_chunk_chars=2000,
            )
        )
        source = "@@KBHEADING_000001@@\n\n伤害为 15%，详见 @@KBREF_000002@@。"
        protected = {
            "@@KBHEADING_000001@@": "2 级标题“数据”",
            "@@KBREF_000002@@": "引用“详情” -> ./详情.md",
        }
        with patch.object(cleaner, "_chat", return_value=source):
            self.assertEqual(
                cleaner.clean_markdown(
                    source,
                    title="测试",
                    protected_values=protected,
                ),
                source,
            )
        with patch.object(
            cleaner,
            "_chat",
            return_value=source.replace("@@KBREF_000002@@", ""),
        ):
            with self.assertRaises(LLMCleaningError):
                cleaner.clean_markdown(source, title="测试", protected_values=protected)
        with patch.object(cleaner, "_chat", return_value=source.replace("15%", "20%")):
            with self.assertRaises(LLMCleaningError):
                cleaner.clean_markdown(source, title="测试", protected_values=protected)

    def test_interrupted_write_is_reconciled_from_document_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "raw"
            output = root / "docs"
            self._write_source(source)
            transform_dataset(source, output)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            document = output / "忍者猴.md"
            old_text = document.read_text(encoding="utf-8")
            archive = output / ".history" / "忍者猴" / "v0001.md"
            archive.parent.mkdir(parents=True)
            archive.write_text(old_text, encoding="utf-8")
            document.write_text(
                old_text.replace("document_version: 1", "document_version: 2").replace(
                    "llm_cleaned: false", "llm_cleaned: true"
                ),
                encoding="utf-8",
            )

            count = reconcile_interrupted_documents(
                output_root=output,
                documents=manifest["documents"],
                cleaning_profile="markdown-v2:llm=test",
            )

            self.assertEqual(count, 1)
            record = manifest["documents"]["忍者猴.md"]
            self.assertEqual(record["document_version"], 2)
            self.assertTrue(record["llm_cleaned"])
            self.assertEqual(record["history"][0]["archive_path"], ".history/忍者猴/v0001.md")

    def test_structural_spacing_keeps_headings_and_tables_separate(self) -> None:
        compact = "正文\n## 数据\n| 等级 | 价格 |\n| --- | --- |\n| 1 | 250 |"
        normalized = normalize_structural_spacing(compact)
        self.assertIn("正文\n\n## 数据\n\n| 等级 | 价格 |", normalized)

    def test_crawler_canonicalizes_revision_urls_and_filters_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            spider = WikiSpider(
                "https://wiki.biligame.com/btd6/%E9%A6%96%E9%A1%B5",
                output_dir=temp_dir,
            )
            revision = (
                "https://wiki.biligame.com/btd6/index.php?"
                "title=%E5%BF%8D%E8%80%85%E7%8C%B4&oldid=123#x"
            )
            self.assertEqual(
                spider.normalize_url(revision),
                "https://wiki.biligame.com/btd6/%E5%BF%8D%E8%80%85%E7%8C%B4",
            )
            self.assertFalse(
                spider.is_valid_url("https://wiki.biligame.com/btd6/特殊:贡献者/忍者猴")
            )


if __name__ == "__main__":
    unittest.main()

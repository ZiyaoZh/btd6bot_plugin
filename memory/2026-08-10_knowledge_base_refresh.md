# 2026-08-10 更新 BTD6 Wiki 知识库

## 变更类型
maintenance

## 更新方式
- 在 `btd6_knowledge_base` 目录执行现有的增量更新入口：

```bash
python3 kb_interface.py ingest \
  --url "https://wiki.biligame.com/btd6/%E9%A6%96%E9%A1%B5" \
  --max-pages 1000 \
  --delay 0.2 \
  --raw-output wiki_data \
  --markdown-output knowledge_docs \
  --overwrite-existing \
  --llm-clean \
  --env-file ../../../.env \
  --workers 4
```

## 更新结果
- 抓取完成：访问 620 个页面，知识库包含 619 篇 active 文档。
- 转换完成：618 篇转换，1 篇源码未变化而跳过，LLM 清理失败和转换失败均为 0。
- 新增 32 篇文档，包括 `天卫.md`、天卫的 15 个升级页面、`天赋.md`、`警长.md`、`社交赛季.md` 和 `回合列表/边境传奇.md`。
- 清理配置为 `markdown-v2:llm=deepseek-v4-flash`。
- 抓取器记录 134 个失败 URL，其中大部分为历史遗留的特殊页或旧版本链接。本次新增的两个有效页面 404 为 `装甲巨兽` 和 `BOSS活动/攻略`。
- 链接报告剩余 2 个未解析引用，均为 `BOSS活动/攻略.md`，分别来自 `BOSS活动.md` 和 `Boss活动.md`；对应源站页面不存在。

## 兼容修正
- Wiki 在 `南方巨兽龙.md` 的“每个野兽之力详细数据”表头使用了 `colspan=999`，转换后会产生 999 列表格。
- 将该表限制为实际使用的 8 列，并保留本次抓取后的最新数值；同步更新 `manifest.json` 内容哈希。
- 清理 `回合.md` 和 `地图.md` 中两处生成内容的行尾空格，并同步更新内容哈希。

## 验证
- 619 篇文档的 Markdown 内容哈希和原始 HTML 哈希全部与 `manifest.json` 一致，缺失文件为 0。
- `南方巨兽龙.md` 目标表格最大 8 列，不存在超宽行。
- `python3 ../tests/test_markdown_knowledge_base.py -v`：7 项通过。
- `python3 ../tests/evaluate_knowledge_search.py`：53/53 通过，检索命中率和首文档成功率均为 1.000。
- `python3 ../tests/evaluate_knowledge_grounding.py`：53/53 通过，grounded answer rate 为 1.000。
- 除必须保持原始字节和源码哈希的 Wiki HTML 快照外，其余生成内容通过 `git diff --check`。

## 四位一体
- Code/Data: `btd6_knowledge_base/wiki_data`, `btd6_knowledge_base/knowledge_docs`
- Test: `tests/test_markdown_knowledge_base.py`, `tests/evaluate_knowledge_search.py`, `tests/evaluate_knowledge_grounding.py`
- Docs: 更新方式已由 `btd6_knowledge_base/README.md` 记录
- Skill: 不涉及 Agent 技能知识变更

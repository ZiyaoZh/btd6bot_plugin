# 2026-10-12 更新 BTD6 内容与 Wiki 知识库

## 参考基线

- 内容目录：`BTD6ApiExplorer/BTD6ApiExplorer.github.io`。
- 参考提交：`c9d5e3fc5d379e2f37076a8052cefc1beca8d8f2`。
- 提交时间：`2026-10-07T01:50:39-04:00`。
- `Constants.json`：`version=3.0.0`（参考项目版本），`projectContentVersion=57`（游戏内容版本）。
- 目录规模：26 种猴塔、18 位英雄、88 张地图、7 个 Boss。

## 内容同步

- `btd6_api_processor/translate.md` 新增 `ShipCapture → 夺船大战`，中文名沿用本次抓取的中文 Wiki，不单独宣称官方译名。
- `btd6_knowledge_base/aliases.json` 新增 `ShipCapture`、`Ship Capture` 与“夺船大战”的互查别名，并补充丹迪蒙克的中文名称兼容。
- 活动塔分类、收集活动的有序猴塔池、开箱池及两套 Insta 素材均已匹配参考目录，无需新增素材；本次复制素材数量为 0。

## 知识库更新

使用现有 `kb_interface.py ingest` 从 Wiki 首页抓取，启用覆盖已有 HTML、增量转换和 LLM 清洗。抓取结束后通过 `convert --workers 12` 继续转换；已完成文档跳过，断线回退页自动重试。

- 本次遍历 624 个 URL，实际请求失败的页面为 `BOSS活动/攻略` 和“装甲巨兽”，均返回 404。
- 爬虫元数据中的 `failed_count=134` 包含历史失败记录，不能当作本次实际请求失败数。
- 文档库由 619 篇增至 625 篇，全部文档更新到本次源码快照，旧版保存在 `.history`。
- 新增文档：`S.P.I.D.E.R_Monkey.md`、`专业能力挑战.md`、`丹迪蒙克.md`、`回合列表/精通模式.md`、`天空守卫者的任务.md`、`精通模式任务.md`。
- 清洗配置：`markdown-v2:llm=gpt-6-luna`，沿用当前环境的知识库清洗配置。
- 最终转换：`source_documents=625`、`failed=0`、`llm_failed=0`、`source_missing=0`。
- 最后一次转换处理 412 篇、跳过 213 篇；这是续跑统计，完整更新覆盖全部 625 篇。
- 内部链接报告剩余 2 个引用，分别来自 `BOSS活动.md`、`Boss活动.md`，均指向源站不存在的 `BOSS活动/攻略.md`。

## 数据格式修整

Wiki 的超大 `colspan` 会产生大量空列。本次将 14 篇文档中的超宽表格按实际有内容的列宽修整，不删除数据，同步更新 manifest 内容哈希：

- 南方巨兽龙、霸王龙：8 列。
- 名词解释、攻击目标：9 列。
- 大白鲨、巨齿鲨、逆戟鲸：7 列。
- 哈斯特巨鹰、巨型秃鹫、角鸮、迅猛龙、金雕：5 列。
- 恶灵龙、梭子鱼：4 列。

另修整 `Prices.json.md`、`回合.md`、`地图.md` 的行尾空格。原始 HTML 保持源站字节，源码哈希不变。

## 验证

- 625 篇文档的正文 SHA-256、原始 HTML SHA-256 全部匹配 manifest，历史归档无缺失，全部 `llm_cleaned=true`。
- 内容同步审计：通过，所有消费者匹配参考目录。
- 活动组件测试：21/21 通过。
- 插件主测试集：119/119 通过，包括 Markdown 知识库测试。
- 最终检索评测：53/53 通过，命中率和首位命中率均为 1.000。
- 最终知识读取评测：53/53 通过，grounded answer rate 为 1.000。
- `ShipCapture`、`Ship Capture`、“夺船大战”均首位命中 `地图.md`；`S.P.I.D.E.R Monkey` 首位命中新增文档。
- 翻译解析器实际读取 `ShipCapture` 为“夺船大战”。
- 固定 seed=57 的钻石开箱可抽到天卫，16 种路径素材均能解析，并在临时目录合成 1840×512 的非空图片。
- 各嵌套仓库已检查 `git status --short` 和 `git diff --check`。暂存后再次检查新增文件，当前正文和代码格式通过；原始 HTML 及一份中途回退文档的历史归档保留源字节中的行尾空格，以保持源码和归档哈希。
- 系统 Python 缺少 Pillow，测试时仅在临时目录安装依赖并通过 `PYTHONPATH` 使用；未修改部署依赖或运行时缓存。

本次完成 Wiki 与 GitHub 数据源的联网验证；未向真实 QQ 会话发送测试消息。

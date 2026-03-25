# read_game_knowledge_file

## 用途
读取一个 BTD6 知识库文件内容，用于生成更准确的回答。

## 指令名
- read_game_knowledge_file

## 参数
- file_path: str，必填。建议直接使用 search_game_knowledge 返回的 matched_paths 项。
- max_chars: int，可选。默认 8000，建议范围 500-20000。
- keywords: str，可选。用于长文关键词提取；当文档超过 8000 字符时建议必填。

## 安全限制
- 仅允许读取 btd6_knowledge_base/wiki_data_second_cleaned/btd6 目录内文件。
- 兼容读取历史目录 btd6_knowledge_base/wiki_data_cleaned/btd6（仅用于旧索引结果回放）。
- 禁止读取该目录之外的任意路径（包括 ../ 路径穿越、绝对路径越权）。
- 仅允许 html/htm/md/txt 文件。

## 返回
- command_result.data.file_path: 实际读取路径
- command_result.data.content: 清洗后的文本内容
- command_result.data.max_chars: 返回内容上限
- command_result.data.mode: full 或 extract
- command_result.data.keywords: 提取模式下使用的关键词

## 示例
- {"module":"llm_processor","target":"command_processor","intent":"read_game_knowledge_file","arguments":{"file_path":"btd6_knowledge_base/wiki_data_second_cleaned/btd6/towers/dart-monkey.html","max_chars":6000},"reply":"","reason":"读取候选知识文件"}
- {"module":"llm_processor","target":"command_processor","intent":"read_game_knowledge_file","arguments":{"file_path":"btd6_knowledge_base/wiki_data_second_cleaned/btd6/towers/moab-eliminator.html","keywords":"消除器 大炮 价格 伤害","max_chars":8000},"reply":"","reason":"长文按关键词提取"}

## 长文策略
- 当文档长度超过 8000 字符时，系统不再返回整篇文档。
- 会调用知识库 extract API（按 file + keywords）提取相关文本块后返回。

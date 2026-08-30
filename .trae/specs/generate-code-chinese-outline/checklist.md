# Checklist

## LLM 调用层（Task 1）
- [ ] opencode 订阅 LLM 调用层已封装，支持 OpenAI 兼容端点 `/v1/chat/completions` 与 AK/SK 鉴权
- [ ] 角色级模型切换已实现：`EXTRACT_LLM_MODEL=qwen2.5-coder`（中文化）、`QUERY_LLM_MODEL=deepseek-v3`（推理），同一 client 仅切换 `model` 字段
- [ ] 请求重试与超时已实现：`LLM_MAX_RETRY=3`、`LLM_TIMEOUT_MS=30000`
- [ ] token 用量统计已落盘（prompt/completion token 数、模型名、角色），用量表可查
- [ ] 配额受限兜底已实现：429 / 连续 3 次超时 → 本地小模型降级，响应标记 `degraded=true` 且不抛阻断异常
- [ ] 兜底成功率 ≥ 99%（配额受限场景压测验证）

## 函数级中文大纲生成器（Task 2）
- [ ] 基于 AST 切分到函数已实现，复用 CodeGraph tree-sitter 多语言符号提取，输出 `AstFunctionNode`
- [ ] 中文大纲 Prompt 满足：保留类名/函数名、实现逻辑中文分步描述（每步一句）、标注参数/返回值/副作用/异常路径/外部调用
- [ ] 输出结构化 `CodeOutline` JSON（symbol/file/cn_summary/external_calls/failure_paths），通过 JSON Schema 校验
- [ ] 符号保留率 = 100%（symbol/类名/函数名原文不被翻译）
- [ ] 中文大纲语义准确率 ≥ 90%（抽样人工评审）
- [ ] 外部调用检出率 ≥ 85%（DB/RPC/缓存调用进入 `external_calls`）
- [ ] 异常路径检出率 ≥ 80%（try/catch、自定义抛出、提前 return 进入 `failure_paths`）
- [ ] 大函数（> 200 行）按 AST 逻辑块拆分，`cn_summary` ≤ 512 字符
- [ ] 多语言（Java/Go/Python/TypeScript）函数边界正确提取，生成不因语言降级

## REST 接口与 MCP 工具（Task 3）
- [ ] `POST /api/v1/code2cn/generate` 已实现，输入 symbol/file/source_code，输出 CodeOutline JSON（含 `degraded`、`model`、`tokens`）
- [ ] MCP 工具 `code2cn_outline(symbol)` 已注册，返回完整 CodeOutline
- [ ] 接口错误码齐全：401 鉴权失败、503 配额耗尽且无兜底
- [ ] 缓存命中响应 P95 < 50ms；未命中端到端 < 5s（单函数）

## CodeGraph 节点语义增强（Task 4）
- [ ] CodeGraph 函数节点 schema 已升级：新增 `cn_summary` / `external_calls` / `failure_paths` 字段并持久化到 SQLite
- [ ] 旧节点回填迁移已实现：首次查询识别缺失字段按需中文化，不阻塞已有查询
- [ ] MCP 查询返回 `cn_summary`，缓存命中 P95 < 50ms，未命中 < 5s

## LightRAG 实体描述注入（Task 5）
- [ ] "函数符号 + 中文描述"已封装为 LightRAG 实体描述（实体名 `func:<symbol>`，description 为中文大纲全文）
- [ ] 经 `insert_custom_kg` 注入检索图谱，embedding 用 bge-m3 且 dim=1024
- [ ] 中文语义检索可命中（如"创建订单时库存校验失败"召回 `func:OrderService.create`），Recall@5 达标
- [ ] 注入 20 样例实体后，10 条中文 query 检索 Recall@5 ≥ 阈值

## 增量中文化策略（Task 6）
- [ ] 增量中文化已实现，仅对嫌疑子图函数按需触发中文化
- [ ] `cn_summary` 缓存层已实现（`CODE2CN_CACHE_ENABLED`），已中文化函数直接命中
- [ ] `git commit` 增量重建已实现：基于 `git diff` 仅重建受影响函数及调用链上下游
- [ ] 增量中文化 token 成本 ≤ 全仓的 30%（100 函数仓库：T_inc / T_full ≤ 0.30）

## 分层摘要升级路径（Task 7）
- [ ] 分层摘要（丙方案）已实现：方法摘要→类摘要→模块摘要多 Agent 自底向上（Agent4cs 范式）
- [ ] 规模阈值触发切换已实现（`CODE2CN_HIERARCHICAL_THRESHOLD=5000` 函数数）
- [ ] LightRAG 实体同时注入方法级与类/模块级实体，三级均可检索命中

## 质量与成本验收（Task 8）
- [ ] `scripts/eval_code2cn.py` 质量报告产出全量指标（准确率/符号保留率/检出率/成本比/兜底成功率）
- [ ] 全量指标达标并归档：符号保留率 100%、语义准确率 ≥ 90%、外部调用检出率 ≥ 85%、异常路径检出率 ≥ 80%、增量成本 ≤ 全仓 30%、兜底成功率 ≥ 99%

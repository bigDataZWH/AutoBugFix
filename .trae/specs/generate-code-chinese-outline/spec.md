# 代码中文描述模块 Spec

## Why
CodeGraph 提取的函数符号节点仅有符号名、缺少中文语义，无法被中文根因检索直接命中。本模块将代码（保留类名/函数名原文符号、实现逻辑用中文分步描述）转化为函数级中文大纲，作为图谱节点的语义摘要喂给 LightRAG 做语义检索与根因推理。复用 opencode 订阅大模型（DeepSeek-V3 / Qwen2.5-Coder），不重复部署 LLM、不重复造轮子，采用 Google NL Outlines（FSE'25）范式。

## What Changes
- 新增函数级中文大纲生成能力（方案甲）：基于 AST 切分到函数，逐函数调用 opencode 订阅 LLM 生成中文大纲，保留原文类名与函数名，实现逻辑用中文分步描述（每步一句）。
- 新增中文化 Prompt 规范：输出结构化 `CodeOutline` JSON（`symbol` / `file` / `cn_summary` / `external_calls` / `failure_paths`），标注输入参数、返回值、副作用、异常路径、外部调用（DB/RPC/缓存）。
- 新增 REST 接口 `POST /api/v1/code2cn/generate`（输入 `symbol` / `file` / `source_code`，输出 `CodeOutline` JSON）。
- 新增 MCP 工具 `code2cn_outline(symbol)`，供 A2 代码分析 Agent 调用获取函数中文大纲。
- 新增 LLM 角色级模型配置：抽取用便宜模型（`EXTRACT_LLM_MODEL`）、推理用强模型（`QUERY_LLM_MODEL`），支持配额受限时本地小模型兜底。
- 新增 CodeGraph 节点语义增强：每个函数符号节点附加 `cn_summary` 字段并持久化（**BREAKING**：CodeGraph 函数节点 schema 新增 `cn_summary` 必填字段，旧节点需回填）。
- 新增 LightRAG 衔接：将"函数符号 + 中文描述"作为实体描述经 `insert_custom_kg` 注入检索图谱。
- 新增增量中文化策略：仅对根因分析涉及的嫌疑子图函数按需中文化，控制 token 成本；`git commit` 后仅重建受影响子树。
- 提供分层摘要升级路径（方案丙）：方法摘要→类摘要→模块摘要的多 Agent 自底向上流程（Agent4cs 范式），供大规模仓库按阈值切换。
- 四方案对比纳入决策记录：甲·函数级 NL 大纲（推荐，主路径）、乙·文件级摘要（语义质量中，不主推）、丙·分层摘要（token 成本高，大规模升级）、丁·现成代码注释工具（非 LLM、语义质量低，不推荐）。

## Impact
- Affected specs: `build-codegraph-knowledge-graph`（函数节点 schema 新增 `cn_summary` 字段，依赖本模块输出）、`setup-lightrag-retrieval-engine`（实体描述数据源，经 `insert_custom_kg` 注入）、`orchestrate-five-agent-engine`（A2 代码分析 Agent 依赖中文大纲进行根因推理）、`analyze-rootcause-with-suspect-subgraph`（嫌疑子图触发按需中文化）
- Affected code: 语义化引擎模块 `code2cn`（核心生成器、Prompt 模板、REST 控制器、MCP 工具注册）、CodeGraph 节点增强适配层（schema 迁移 + `cn_summary` 写入）、LightRAG 注入适配层、opencode LLM 调用封装（角色级 client + token 统计 + 限流兜底）、AST 函数切分器（复用 tree-sitter / CodeGraph 符号提取）

## ADDED Requirements

### Requirement: 函数级中文大纲生成
系统 SHALL 提供"函数级 NL 大纲"生成能力，将函数签名 + 函数源码转换为中文功能大纲，保留原文类名与函数名（便于回溯定位），实现逻辑用中文分步描述（每步一句、简洁），并标注输入参数、返回值、副作用、异常路径以及外部调用（DB/RPC/缓存）。输出须为结构化 `CodeOutline` JSON。

#### Scenario: 正常函数中文化
- **WHEN** 传入 `OrderService.create` 的函数签名与源码
- **THEN** 输出 JSON 包含 `symbol="OrderService.create"`、`file="OrderService.java:42"`、`cn_summary="创建订单：1.校验参数 2.查库存 3.写入订单表 4.发MQ 5.返回订单号"`、`external_calls=["库存RPC","订单表DB","MQ发送"]`、`failure_paths=["库存不足抛InsufficientException"]`

#### Scenario: 含外部调用函数中文化
- **WHEN** 传入含 DB 读写、RPC 调用、缓存访问的函数源码（如 `PaymentService.settle`）
- **THEN** 输出 `external_calls` 数组显式列出全部外部依赖（如 `["支付RPC","Redis缓存","交易表DB"]`），且 `cn_summary` 在对应步骤标出外部调用

#### Scenario: 含异常路径函数中文化
- **WHEN** 传入含 `try/catch`、抛出自定义异常、提前 `return` 的函数源码
- **THEN** 输出 `failure_paths` 数组枚举各异常分支（如 `["余额不足抛BalanceException","超时重试3次后抛TimeoutException"]`），`cn_summary` 不遗漏异常分支

#### Scenario: 大函数拆分中文化
- **WHEN** 传入单函数源码超过 200 行或圈复杂度过高
- **THEN** 系统将其按 AST 逻辑块切分为多段，分别生成中文分步描述并标注段间数据流，`cn_summary` 步骤数按段聚合且不超长（单函数 `cn_summary` ≤ 512 字符）

#### Scenario: 多语言函数中文化
- **WHEN** 传入 Java / Go / Python / TypeScript 不同语言的函数源码
- **THEN** AST 切分器复用 CodeGraph 的多语言 tree-sitter 解析能力正确提取函数边界，`symbol` 命名遵循 `类名.方法名` 或 `包名.函数名` 约定，中文大纲生成不因语言差异降级

### Requirement: LLM 角色级模型切换与配额兜底
系统 SHALL 复用 opencode 已订阅大模型，通过 OpenAI 兼容端点与 AK/SK 鉴权调用，并支持角色级模型配置：抽取/中文化用便宜模型（`EXTRACT_LLM_MODEL`），根因推理用强模型（`QUERY_LLM_MODEL`）。当订阅配额受限时，自动降级至本地小模型兜底，保证流程不中断。

#### Scenario: 抽取用便宜模型
- **WHEN** 系统执行函数中文大纲生成（中文化阶段）
- **THEN** 调用 `EXTRACT_LLM_MODEL`（默认 `qwen2.5-coder`）生成 `CodeOutline`，单次调用 token 用量被统计上报

#### Scenario: 推理用强模型
- **WHEN** A2 代码分析 Agent 基于中文大纲做根因推理
- **THEN** 调用 `QUERY_LLM_MODEL`（默认 `deepseek-v3`），复用同一 LLM client 仅切换模型标识

#### Scenario: LLM 配额受限兜底
- **WHEN** opencode 订阅模型返回 429/配额耗尽或连续 3 次超时
- **THEN** 系统自动降级至本地小模型（兜底）生成降级大纲（仅 `cn_summary`、`external_calls` 置空、`failure_paths` 置空），并在响应中标记 `degraded=true`，不抛出阻断错误

#### Scenario: token 用量可观测
- **WHEN** 任意 LLM 调用完成
- **THEN** 系统记录输入/输出 token 数、模型名、调用角色，汇总后用于增量成本核算与配额预警

### Requirement: 增量按需中文化
系统 SHALL 仅对根因分析涉及的嫌疑子图函数按需触发中文化，而非全仓函数；并在 `git commit` 后仅重建受影响子树的中文大纲，控制 token 成本（目标：增量中文化 token 成本 ≤ 全仓的 30%）。

#### Scenario: 嫌疑子图触发中文化
- **WHEN** 根因分析定位到嫌疑子图函数集合（如 5–20 个函数）
- **THEN** 仅对该子图内未中文化函数触发中文化，已中文化函数命中缓存直接返回

#### Scenario: git commit 增量重建
- **WHEN** 仓库发生 `git commit` 且变更触及部分函数
- **THEN** 系统基于 `git diff` 计算受影响子树，仅重建受影响函数及调用链上下游的中文大纲，未受影响节点保持不变

#### Scenario: 全仓兜底
- **WHEN** 用户显式请求全仓中文化或首次冷启动无缓存
- **THEN** 系统执行全仓中文化并写入缓存，后续按需命中，全仓成本仅发生一次

### Requirement: CodeGraph 节点语义增强
系统 SHALL 将中文化输出附加到 CodeGraph 提取的每个函数符号节点，作为节点描述属性 `cn_summary` 并持久化，使节点同时具备可追溯符号名与可检索中文语义。

#### Scenario: 节点增强写入
- **WHEN** 函数中文大纲生成完成
- **THEN** 对应 CodeGraph 函数节点的 `cn_summary` 字段被写入 SQLite 并持久化，`external_calls`、`failure_paths` 同字段落地

#### Scenario: 旧节点回填迁移
- **WHEN** CodeGraph 中存在无 `cn_summary` 的历史函数节点（schema 升级后）
- **THEN** 系统在首次查询时识别缺失字段并按需触发中文化回填，不阻塞已有查询能力

#### Scenario: MCP 查询返回
- **WHEN** AI 工具经 MCP 调用 `code2cn_outline(symbol)`
- **THEN** 返回包含 `cn_summary` 的完整节点信息，命中缓存 < 50ms，未命中触发生成

### Requirement: LightRAG 实体描述注入
系统 SHALL 将"函数符号 + 中文描述"作为实体描述，经 LightRAG `insert_custom_kg` 注入检索图谱，使其可被中文语义检索命中。实体描述经 bge-m3（dim=1024）嵌入向量化。

#### Scenario: 实体注入
- **WHEN** 函数中文化与 CodeGraph 节点增强完成
- **THEN** LightRAG 图谱中存在实体 `func:OrderService.create`，其 description 为中文大纲全文，embedding 由 bge-m3 生成（dim=1024）

#### Scenario: 中文语义检索命中
- **WHEN** 根因分析以中文自然语言检索（如"创建订单时库存校验失败"）
- **THEN** 检索结果召回 `func:OrderService.create` 实体并返回其中文大纲，召回率（Recall@5）达标

### Requirement: 分层摘要升级路径
系统 SHALL 在大规模仓库场景下支持分层摘要（方案丙，多 Agent 自底向上：方法摘要→类摘要→模块摘要，Agent4cs 范式）作为函数级大纲的升级选项，仍复用 opencode 订阅 LLM，保证语义质量不下降。

#### Scenario: 规模阈值触发切换
- **WHEN** 仓库函数规模超过阈值（默认 > 5000 函数或单函数平均 > 300 行）
- **THEN** 系统提示切换至分层摘要模式，用户确认后启用多 Agent 自底向上流程

#### Scenario: 分层语义聚合
- **WHEN** 分层摘要模式启用
- **THEN** 自底向上依次生成方法摘要、类摘要、模块摘要，类/模块摘要由其子方法大纲聚合而成，LightRAG 实体同时包含方法级与类/模块级实体

## 技术细节

### 接口定义

REST 端点：

```
POST /api/v1/code2cn/generate
Content-Type: application/json
Authorization: Bearer <AK/SK>

Request:
{
  "symbol": "OrderService.create",
  "file": "OrderService.java:42",
  "source_code": "<函数源码字符串>"
}

Response 200:
{
  "symbol": "OrderService.create",
  "file": "OrderService.java:42",
  "cn_summary": "创建订单：1.校验参数 2.查库存 3.写入订单表 4.发MQ 5.返回订单号",
  "external_calls": ["库存RPC", "订单表DB", "MQ发送"],
  "failure_paths": ["库存不足抛InsufficientException"],
  "degraded": false,
  "model": "qwen2.5-coder",
  "tokens": {"prompt": 320, "completion": 96}
}

Response 503 (配额耗尽且无兜底):
{ "error": "llm_quota_exhausted", "degraded": true }
```

MCP 工具签名：

```
tool name: code2cn_outline
params:
  symbol: string  // 形如 "OrderService.create"，必填
returns:
  CodeOutline {
    symbol: string,
    file: string,            // 含行号，如 "OrderService.java:42"
    cn_summary: string,     // 中文分步描述
    external_calls: string[],
    failure_paths: string[],
    degraded: boolean
  }
```

### 数据结构

`CodeOutline` JSON schema：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CodeOutline",
  "type": "object",
  "required": ["symbol", "file", "cn_summary"],
  "properties": {
    "symbol": { "type": "string", "description": "原文符号名，保留类名/函数名，如 OrderService.create" },
    "file": { "type": "string", "description": "文件路径含行号，如 OrderService.java:42" },
    "cn_summary": { "type": "string", "maxLength": 512, "description": "中文分步描述，每步一句" },
    "external_calls": { "type": "array", "items": { "type": "string" }, "description": "DB/RPC/缓存等外部调用" },
    "failure_paths": { "type": "array", "items": { "type": "string" }, "description": "异常/失败分支" },
    "degraded": { "type": "boolean", "default": false, "description": "是否降级兜底生成" }
  }
}
```

AST 函数节点 schema（CodeGraph 符号提取输出，作为中文化输入）：

```json
{
  "title": "AstFunctionNode",
  "type": "object",
  "required": ["symbol", "file", "start_line", "end_line", "source_code"],
  "properties": {
    "symbol": { "type": "string" },
    "file": { "type": "string" },
    "start_line": { "type": "integer" },
    "end_line": { "type": "integer" },
    "source_code": { "type": "string" },
    "language": { "type": "string", "enum": ["java", "go", "python", "typescript"] },
    "signature": { "type": "string", "description": "函数签名，含参数与返回类型" }
  }
}
```

### 配置项

```ini
# LLM 角色级配置
EXTRACT_LLM_MODEL=qwen2.5-coder        # 抽取/中文化用便宜模型
QUERY_LLM_MODEL=deepseek-v3            # 推理/根因分析用强模型
LLM_BASE_URL=https://<opencode兼容端点>/v1
LLM_AUTH_TYPE=ak_sk                    # AK/SK 鉴权

# 兜底
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_MODEL=<本地小模型>
LLM_MAX_RETRY=3
LLM_TIMEOUT_MS=30000

# 嵌入
EMBED_MODEL=bge-m3
EMBED_DIM=1024

# 增量与缓存
CODE2CN_CACHE_ENABLED=true
CODE2CN_HIERARCHICAL_THRESHOLD=5000    # 函数数阈值，触发分层摘要(丙方案)
CODE2CN_MAX_FN_LINES=200               # 超过则按逻辑块拆分
CODE2CN_SUMMARY_MAX_CHARS=512
```

### Prompt 要点

- 角色：代码语义化专家，复用 opencode 订阅大模型。
- 输入：函数签名 + 函数源码（由 CodeGraph 符号提取获得）。
- 要求：
  1. **保留**类名与函数名（原文符号，便于回溯定位），`symbol` 字段不得翻译。
  2. 实现逻辑用**中文**分步描述（每步一句，简洁），编号 1./2./3.…
  3. 标注输入参数、返回值、副作用、异常路径（`failure_paths` 数组）。
  4. 涉及外部调用（DB/RPC/缓存）显式标出（`external_calls` 数组）。
- 输出：严格 `CodeOutline` JSON，禁止输出多余解释文本。

## 验收指标

- 符号保留率 100%（`symbol` / 类名 / 函数名原文不被翻译）。
- 增量中文化 token 成本 ≤ 全仓中文化的 30%。
- 中文大纲语义准确率 ≥ 90%（抽样人工评审，正确描述函数主逻辑与数据流）。
- 外部调用检出率 ≥ 85%（DB/RPC/缓存调用被正确识别进 `external_calls`）。
- 异常路径检出率 ≥ 80%（`try/catch`、自定义抛出、提前 `return` 进入 `failure_paths`）。
- MCP 工具 `code2cn_outline` 缓存命中响应 < 50ms；未命中端到端 < 5s（单函数）。
- LLM 配额受限兜底成功率 ≥ 99%（降级不阻断主流程）。

## UT 测试方案

测试框架：Python `pytest` + `pytest-asyncio` + `pytest-mock`；被测组件按模块隔离，LLM / DB / 缓存 / MCP 外部依赖全部 mock。目标覆盖率 ≥ 80%（line + branch）。每个用例包含：用例名、被测组件、输入、预期输出、mock 策略。

| # | 用例名 | 被测组件 | 输入 | 预期输出 | mock 策略 |
|---|--------|----------|------|----------|-----------|
| 1 | `test_codeoutline_schema_validation` | CodeOutline JSON Schema 校验器 | ①缺 `symbol` 字段 ②`symbol` 类型为 int ③`cn_summary` 超 512 字符 ④合法完整 JSON | ①②③ 抛 `ValidationError` 并给出字段级错误；④ 通过校验 | 无 mock（纯 schema 校验，使用 `jsonschema` 直验） |
| 2 | `test_ast_function_splitter` | AST 函数切分器（复用 tree-sitter） | Java / Go / Python / TypeScript 各 1 个含多函数的源码片段 | 正确提取每个函数边界，输出 `AstFunctionNode` 列表（`symbol` / `start_line` / `end_line` / `signature` / `source_code` 完整） | mock CodeGraph tree-sitter 解析桩，断言函数数量与边界行号 |
| 3 | `test_prompt_construction` | 中文化 Prompt 模板构造器 | `AstFunctionNode`（含签名 + 源码） | Prompt 含"保留原文符号"指令与"标注外部调用（DB/RPC/缓存）"指令，占位符正确填充且 `symbol` 原文未被翻译 | 无 mock（纯模板渲染，字符串断言） |
| 4 | `test_llm_role_switch` | LLM client 角色级模型路由 | 抽取角色调用 vs 推理角色调用 | 抽取走 `EXTRACT_LLM_MODEL=qwen2.5-coder`、推理走 `QUERY_LLM_MODEL=deepseek-v3`，同一 client 仅 `model` 字段不同 | mock OpenAI 兼容 `/v1/chat/completions`，断言请求体 `model` 字段值 |
| 5 | `test_llm_quota_fallback` | LLM client 配额兜底逻辑 | 连续 3 次 429 / 连续 3 次超时 | 降级走本地小模型，返回 `degraded=true`，`external_calls` / `failure_paths` 置空，无异常抛出 | mock client 依次抛 `429` / `asyncio.TimeoutError`，mock 兜底模型返回基础大纲 |
| 6 | `test_external_call_detection` | 外部调用识别器 | 含 DB 读写 / RPC 调用 / 缓存访问的函数源码 | `external_calls` 数组准确列出 DB / RPC / 缓存三类，检出率 ≥ 85% | mock LLM 返回标注结果，断言数组内容与召回率 |
| 7 | `test_failure_path_extraction` | 异常路径提取器 | 含 `try/catch` / 抛自定义异常 / 提前 `return` 的源码 | `failure_paths` 枚举各异常分支，不遗漏提前返回路径 | mock LLM 返回异常分支，断言数组覆盖各分支 |
| 8 | `test_large_function_chunking` | 大函数拆分器 | > 200 行（超 `CODE2CN_MAX_FN_LINES`）函数源码 | 按 AST 逻辑块切分为 ≥ 2 段，分别生成后聚合 `cn_summary` 且 ≤ 512 字符 | mock LLM 逐段返回分步描述，断言段数与 summary 长度 |
| 9 | `test_cache_hit_miss` | 缓存层（`CODE2CN_CACHE_ENABLED`） | 同一 `symbol` 连续两次请求 | 首次未命中触发生成、二次命中直接返回缓存，命中路径 P95 < 50ms | mock LLM（仅首次调用）、mock 缓存 backend，`time.perf_counter` 采样 ≥ 100 次计时断言 |
| 10 | `test_mcp_tool_response` | MCP 工具 `code2cn_outline(symbol)` | `symbol` 参数（如 `OrderService.create`） | 返回 `CodeOutline` 完整结构（`symbol` / `file` / `cn_summary` / `external_calls` / `failure_paths` / `degraded` 六字段齐全）且 Schema 合规 | mock 生成器返回固定 outline，断言响应 Schema 校验通过 |

> 用例 4、5、9 为 async 用例（`@pytest.mark.asyncio`），使用 `pytest-mock` 的 `mocker.patch` 替换 LLM client 与缓存 backend；用例 9 的 P95 时序断言使用 `time.perf_counter` 采样 ≥ 100 次。用例 6、7 的检出率阈值与"验收指标"对齐（外部调用 ≥ 85%、异常路径 ≥ 80%）。

## E2E 测试方案

测试框架：FastAPI 接口用 `httpx.AsyncClient`（`ASGITransport` 直连 app）；MCP 工具用 mock transport；依赖服务（SQLite / Redis 缓存 / git 仓库）用 `testcontainers` 起真实实例以保证全链路真实。每个场景断言点量化可测。

| # | 场景名 | 前置条件 | 测试步骤 | 预期结果 | 断言点 |
|---|--------|----------|----------|----------|--------|
| 1 | `e2e_full_generate_flow` | opencode LLM mock 就绪、CodeGraph 符号提取可用 | 传入 `symbol` + `file` + `source_code` → AST 切分 → LLM 调用 → 输出 `CodeOutline` | 全链路成功产出 JSON | `symbol` / `file` / `cn_summary` / `external_calls` / `failure_paths` 全部非空；JSON Schema 合规；`degraded=false` |
| 2 | `e2e_rest_api_endpoint` | FastAPI app 启动、`testcontainers` Redis 缓存 | `POST /api/v1/code2cn/generate`：①正常请求 ②缺 `source_code` ③LLM 返回 429 ④LLM 内部 500 | 分别返回 200 / 400 / 429 / 500 | 鉴权头缺失→401；缺 `source_code`→400；配额受限→429 + `degraded=true`；LLM 异常→500 |
| 3 | `e2e_mcp_tool_integration` | MCP server 注册 `code2cn_outline`、A2 Agent mock | A2 Agent 经 MCP 调用 `code2cn_outline("OrderService.create")` 并消费返回大纲 | Agent 成功获取并解析大纲用于下游推理 | 返回含 `cn_summary` 的完整 `CodeOutline`；Agent 消费后下游上下文非空 |
| 4 | `e2e_degraded_mode` | opencode LLM 全部 mock 为 429、本地兜底模型可用 | 触发生成 → 3 次重试失败 → 降级兜底 | 返回降级大纲且不阻断 | `degraded=true`；`cn_summary` 仅基础描述非空；`external_calls` / `failure_paths` 为空数组；HTTP 200 |
| 5 | `e2e_incremental_update` | 已中文化仓库（100 函数）、`testcontainers` git 仓库 | 模拟 `git commit` 改动 3 函数 → 触发增量重建 | 仅重建受影响子树 | 仅 3 函数 `cn_summary` 变更、其余节点不变；增量 token ≤ 全仓 30% |

> 场景 2、4 的 LLM 用 `respx` / `httpx_mock` 拦截 opencode 端点返回 429 / 500；场景 5 的 git 操作用 `testcontainers` 提供真实 git 环境或 `pygit2` 临时仓库；所有 E2E 场景断言点须可量化（HTTP 状态码、字段非空、Schema 合规、token 比例）。

## 跨模块集成测试方案

本方案验证 code2cn 与上游（CodeGraph / opencode LLM）及下游（LightRAG / 5-Agent A2）的数据契约打通，聚焦模块边界字段映射与集成点行为。测试框架沿用 `pytest` + `pytest-asyncio` + `pytest-mock`，集成测试置于 `tests/integration/`；外部 LLM 一律 mock，CodeGraph 符号提取使用真实输出（或真实 tree-sitter 解析桩），LightRAG `insert_custom_kg` 与 MCP 传输层 mock 验证调用参数不实际落库。

### 上下游依赖关系表

| 上游模块 | 上游输出数据契约 | 下游模块 | 下游消费数据契约 |
|---|---|---|---|
| CodeGraph（tree-sitter 符号提取） | `AstFunctionNode`（`symbol`/`file`/`start_line`/`end_line`/`source_code`/`language`/`signature`） | code2cn | REST 入参 `symbol`/`file`/`source_code`；内部消费 `AstFunctionNode` 全字段 |
| opencode LLM（DeepSeek-V3 / Qwen2.5-Coder） | OpenAI 兼容 `/v1/chat/completions` 响应（`choices[0].message.content` + `usage` token 统计） | code2cn | LLM 文本输出解析为 `CodeOutline` JSON；token 落盘 |
| code2cn（生成器 + 适配层） | `CodeOutline` JSON（`symbol`/`file`/`cn_summary`/`external_calls`/`failure_paths`/`degraded`） | LightRAG | `insert_custom_kg` 入参：实体 `func:<symbol>` + `description`（中文大纲全文） + `SIMILAR_TO` 边 |
| code2cn（MCP server） | `CodeOutline` JSON（经 MCP `tools/call` 返回） | 5-Agent A2 代码分析 | MCP `code2cn_outline(symbol)` 响应体，A2 注入推理上下文 |

### 集成测试场景

| # | 场景名 | 涉及模块 | 集成点 | 测试步骤 | 预期结果 | 断言点 |
|---|--------|----------|--------|----------|----------|--------|
| 1 | `integ_codegraph_to_code2cn` | CodeGraph → code2cn | `AstFunctionNode` → 生成器入参（`symbol`/`file`/`source_code` 字段映射） | ①从 CodeGraph 真实符号提取获取 `AstFunctionNode` 列表 ②取一节点传入 code2cn 生成器 ③校验字段透传与大纲生成 | code2cn 接收字段与节点一致，产出合法 `CodeOutline` | `outline.symbol == node.symbol`；`outline.file == node.file`；生成器内部 `source_code == node.source_code`；`symbol` 原文未被翻译；Schema 合规 |
| 2 | `integ_code2cn_to_lightrag` | code2cn → LightRAG | `CodeOutline` → `insert_custom_kg`（实体描述 + `SIMILAR_TO` 边） | ①生成 `CodeOutline` ②适配层封装实体 `func:<symbol>` + `description` ③调用 `insert_custom_kg`（mock） ④校验实体与边参数 | LightRAG 收到实体描述与关系边，embedding 由 bge-m3 生成 | `insert_custom_kg` 被调用一次；实体名 `func:<symbol>`；`description == cn_summary` 全文；embedding `dim == 1024`；`SIMILAR_TO` 边 `source`/`target` 正确 |
| 3 | `integ_code2cn_to_agent2_mcp` | code2cn（MCP server）→ 5-Agent A2 | MCP `code2cn_outline(symbol)` 调用与响应消费 | ①A2 mock 经 MCP transport 调用 `code2cn_outline("OrderService.create")` ②code2cn 返回 `CodeOutline` ③A2 消费大纲注入推理上下文 | A2 成功获取并解析大纲，推理上下文含 `cn_summary` | tool call 参数 `symbol == "OrderService.create"`；响应六字段齐全；A2 上下文 `cn_summary` 非空；Schema 合规；缓存命中 < 50ms |
| 4 | `integ_llm_role_integration` | opencode LLM → code2cn（角色级路由） | `EXTRACT_LLM_MODEL` 抽取 → `QUERY_LLM_MODEL` 推理切换 | ①触发中文化调用 `EXTRACT_LLM_MODEL` ②触发 A2 推理调用 `QUERY_LLM_MODEL` ③校验同一 client 切换 `model` 字段 | 抽取走 `qwen2.5-coder`、推理走 `deepseek-v3`，同一 client 仅 `model` 不同 | 抽取请求体 `model == "qwen2.5-coder"`；推理请求体 `model == "deepseek-v3"`；同一 client 实例；token 统计区分 `extract`/`query` 角色 |
| 5 | `integ_full_pipeline_code2cn` | CodeGraph → code2cn → LightRAG → A2 | 全链路：符号→中文化→注入→检索消费 | ①CodeGraph 输出符号 ②code2cn 中文化生成 `CodeOutline` ③LightRAG `insert_custom_kg` 注入 ④A2 中文 query 检索命中并消费 | 全链路贯通，A2 召回对应函数实体并获取中文大纲 | `CodeOutline` Schema 合规；LightRAG 实体存在；A2 中文 query 召回 `func:<symbol>`；端到端 `symbol` 一致；token 累计统计正确 |

> 场景 1 使用真实 CodeGraph 输出（或真实 tree-sitter 解析小型源码片段）；场景 4、5 的 LLM 调用全部 mock；场景 2、5 的 `insert_custom_kg` 用 mock 验证入参不实际写入；所有断言点须可量化（字段相等、Schema 合规、dim=1024、时延阈值、token 角色区分）。

## 测试数据与 Mock 规范

### 测试数据构造策略

- **Fixture 工厂模式**：为 `AstFunctionNode` / `CodeOutline` / LLM 响应 / MCP 报文提供工厂函数（如 `make_ast_node(language="java")`、`make_code_outline()`、`make_llm_response()`），支持参数化覆盖缺字段、超长 `cn_summary`、空数组等边界。
- **conftest.py 共享 fixture**：在 `tests/conftest.py` 与 `tests/integration/conftest.py` 注册跨用例共享 fixture（LLM mock client、CodeGraph 符号桩、LightRAG ainsert mock、MCP transport mock、tmp 数据库），避免重复构造，目标被测组件 fixture 覆盖率 ≥ 90%。
- **数据生成器**：批量生成多语言样例函数（Java/Go/Python/TypeScript 各 ≥ 5 个），供检出率与全链路场景使用；超长函数生成器产出 > 200 行源码供拆分测试。

### Mock 数据样本

**CodeOutline 样本 JSON**（完整字段，含 `external_calls` 与 `failure_paths`）：

```json
{
  "symbol": "OrderService.create",
  "file": "OrderService.java:42",
  "cn_summary": "创建订单：1.校验参数 2.查库存 3.写入订单表 4.发MQ 5.返回订单号",
  "external_calls": ["库存RPC", "订单表DB", "MQ发送"],
  "failure_paths": ["库存不足抛InsufficientException"],
  "degraded": false
}
```

**AstFunctionNode 样本 JSON**（Java / Go / Python 各一）：

```json
{
  "symbol": "OrderService.create",
  "file": "OrderService.java:42",
  "start_line": 42,
  "end_line": 68,
  "source_code": "public Long create(OrderDTO dto) { validate(dto); stock.check(dto.getSku()); orderRepo.insert(dto); mq.send(\"order-created\", dto); return dto.getId(); }",
  "language": "java",
  "signature": "public Long create(OrderDTO dto)"
}
```

```json
{
  "symbol": "order.Create",
  "file": "order/service.go:18",
  "start_line": 18,
  "end_line": 40,
  "source_code": "func (s *Service) Create(ctx context.Context, dto OrderDTO) (int64, error) { if err := s.validate(dto); err != nil { return 0, err }; s.stock.Check(ctx, dto.SKU); return s.repo.Insert(ctx, dto) }",
  "language": "go",
  "signature": "func (s *Service) Create(ctx context.Context, dto OrderDTO) (int64, error)"
}
```

```json
{
  "symbol": "payment.settle",
  "file": "payment/service.py:25",
  "start_line": 25,
  "end_line": 50,
  "source_code": "def settle(order_id: str) -> str:\n    order = repo.get(order_id)\n    if order.balance < order.amount:\n        raise BalanceException('余额不足')\n    rpc.call('pay-gateway', order)\n    cache.set(order_id, 'settled')\n    return order_id",
  "language": "python",
  "signature": "def settle(order_id: str) -> str"
}
```

**LLM 响应 Mock**（正常响应 / 429 配额耗尽 / 超时）：

正常响应（OpenAI 兼容 `/v1/chat/completions`）：

```json
{
  "id": "chatcmpl-mock-001",
  "object": "chat.completion",
  "model": "qwen2.5-coder",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\"symbol\":\"OrderService.create\",\"file\":\"OrderService.java:42\",\"cn_summary\":\"创建订单：1.校验参数 2.查库存 3.写入订单表 4.发MQ 5.返回订单号\",\"external_calls\":[\"库存RPC\",\"订单表DB\",\"MQ发送\"],\"failure_paths\":[\"库存不足抛InsufficientException\"],\"degraded\":false}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 320, "completion_tokens": 96, "total_tokens": 416 }
}
```

429 配额耗尽：

```json
{
  "error": {
    "message": "quota exhausted",
    "type": "rate_limit_exceeded",
    "code": "429"
  }
}
```

超时（无响应体，mock 注册为异常）：

```json
{
  "scenario": "llm_timeout",
  "mock_type": "asyncio.TimeoutError",
  "delay_ms": 35000,
  "response": null,
  "note": "mock 使请求超过 LLM_TIMEOUT_MS=30000 后抛 asyncio.TimeoutError，触发连续 3 次超时降级路径"
}
```

**MCP `code2cn_outline` 请求 / 响应 Mock**：

请求：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "code2cn_outline",
    "arguments": { "symbol": "OrderService.create" }
  }
}
```

响应：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"symbol\":\"OrderService.create\",\"file\":\"OrderService.java:42\",\"cn_summary\":\"创建订单：1.校验参数 2.查库存 3.写入订单表 4.发MQ 5.返回订单号\",\"external_calls\":[\"库存RPC\",\"订单表DB\",\"MQ发送\"],\"failure_paths\":[\"库存不足抛InsufficientException\"],\"degraded\":false}"
      }
    ]
  }
}
```

### Mock 规范

| 外部依赖 | Mock 策略 | 断言要点 |
|---|---|---|
| opencode LLM（OpenAI 兼容端点） | 用 `respx` / `httpx_mock` 拦截 `/v1/chat/completions`，按角色返回预设 `CodeOutline` JSON；429 / 超时按场景注入 | 请求体 `model` 字段符合角色；不产生真实网络调用；`mock.assert_called_once` |
| CodeGraph symbol（tree-sitter） | 预设 `symbol`/`file`/`source_code` 桩（或真实 tree-sitter 解析小型源码片段） | 输出 `AstFunctionNode` 字段完整；函数边界行号正确 |
| LightRAG `insert_custom_kg` | mock 适配层 `ainsert_custom_kg`，验证入参不实际写入图谱 | 实体名 / `description` / `SIMILAR_TO` 边参数正确；未触发真实 embedding 计算（或 mock embedding dim=1024） |
| MCP 传输层 | mock MCP transport，捕获 `tools/call` 请求与响应序列化 | tool name == `code2cn_outline`；`arguments.symbol` 正确；响应 `content[0].text` 可解析为 `CodeOutline` |

### 测试数据库初始化

- **CodeGraph 节点持久化**：使用 SQLite `:memory:`（`sqlite3.connect(":memory:")`）初始化函数节点表，隔离且无残留；schema 含 `cn_summary`/`external_calls`/`failure_paths` 字段。
- **LightRAG 存储与缓存**：使用 pytest `tmp_path` 临时目录承载 LightRAG storage 与缓存后端，用例结束自动清理；避免污染真实图谱与 Redis。
- **Embedding 向量库**：mock bge-m3 嵌入返回固定 `dim=1024` 向量，不加载真实模型权重。

### Fixture 文件组织

样本 JSON 文件统一置于 `tests/fixtures/code2cn/`，路径约定如下：

```
tests/fixtures/code2cn/
├── code_outline/
│   ├── order_service_create.json        # CodeOutline 样本
│   └── payment_settle_degraded.json     # 降级大纲样本
├── ast_node/
│   ├── java_order_service.json          # Java AstFunctionNode
│   ├── go_order_create.json             # Go AstFunctionNode
│   └── python_payment_settle.json       # Python AstFunctionNode
├── llm_response/
│   ├── normal_qwen_extract.json         # 正常 LLM 响应
│   ├── 429_quota_exhausted.json         # 429 配额耗尽
│   └── timeout_scenario.json            # 超时 mock 配置
└── mcp/
    ├── code2cn_outline_request.json     # MCP 请求报文
    └── code2cn_outline_response.json     # MCP 响应报文
```

> 约定：`tests/fixtures/<module>/*.json` 按子目录分类；fixture 工厂函数从对应路径加载并支持参数覆盖；所有样本 JSON 须通过对应 Schema 校验（`CodeOutline` / `AstFunctionNode`）后方可入库。

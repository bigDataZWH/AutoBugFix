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

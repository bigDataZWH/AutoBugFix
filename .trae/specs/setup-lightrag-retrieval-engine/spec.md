# LightRAG 检索引擎 Spec

## Why
需要统一检索引擎承载历史经验匹配、根因传播追溯、全局架构理解三类检索意图。RAGFlow 部署重（需 Docker+WSL2+ES+MinIO+Redis+MySQL），Win 本地调试困难；LightRAG（港大 HKUDS，lightrag-hku，MIT）双层级检索（low-level 实体 + high-level 主题）+ 图谱 + 重排，Win 原生 pip 部署、Postgres 一体（KV+向量+图状态单库），复用 opencode 订阅 LLM，索引成本约 1/30 GraphRAG。

## What Changes
- **BREAKING**：弃用 RAGFlow，统一采用 LightRAG（lightrag-hku，MIT）作为统一检索引擎；移除 RAGFlow 相关 Docker Compose、ES/MinIO/Redis/MySQL 编排与依赖。
- 新增 Win 原生 pip 部署 LightRAG（单进程），Postgres 一体存储（KV + 向量 + 图状态单库，pgvector 扩展），无需 Docker/WSL2/vm.max_map_count 调参。
- 新增 opencode 订阅 LLM/嵌入对接封装（`opencode_llm` / `maas_bge_m3`）：通过 OpenAI 兼容端点 + AK/SK 鉴权，角色级模型切换（实体抽取 `qwen2.5-coder`、根因推理 `deepseek-v3`、嵌入 `bge-m3` dim=1024）。
- 新增 `LightRAG(working_dir, llm_model_func, embedding_func)` 初始化契约与 `ainsert` / `ainsert_custom_kg` / `aquery` 异步 API 对接。
- 新增三路检索分层路由表：历史经验匹配 → low-level + hybrid；根因传播追溯 → 图谱遍历（含 CodeGraph 调用边）；全局架构理解 → high-level（主题/社区摘要 + 代码中文大纲聚合）。
- 新增 `QueryParam(mode="hybrid"|"low_level"|"high_level", top_k, ...)` 查询参数契约与 mode 枚举校验。
- 新增 CodeGraph 调用图注入适配层：将 AST 真实调用关系经 `ainsert_custom_kg` 注入结构域（非 LLM 幻觉抽取），支持幂等。
- 新增默认 rerank 开关与三档（相关/模糊/不相关）证据评估输出契约，含重排器超时降级。
- 新增飞轮回写增量更新：仅刷新受影响社区摘要，非全量重建。

## Impact
- Affected specs:
  - generate-code-chinese-outline（实体描述/中文大纲为 `ast_kg` 的 description 注入目标）
  - build-codegraph-knowledge-graph（调用图实体/关系为 `ainsert_custom_kg` 注入来源）
  - orchestrate-five-agent-engine（A5 方案检索 high-level、A4 根因相似案例 low-level）
  - build-dual-gate-flywheel（CRAG 重排器评估证据三档、飞轮回写 `ainsert` 增量）
- Affected code:
  - LightRAG 初始化与配置模块（`.env` 读取 + `LightRAG(...)` 构造）
  - 检索路由层（三路意图 → `QueryParam.mode` 分派）
  - opencode LLM/嵌入对接封装（`opencode_llm` / `maas_bge_m3` async 适配）
  - CodeGraph → LightRAG 注入适配层（`ast_kg` 结构映射 + `ainsert_custom_kg`）
  - 重排与增量更新模块（rerank 三档 + 社区摘要增量）

## ADDED Requirements

### Requirement: LightRAG 部署与存储
系统 SHALL 在 Win11 上以 `pip install lightrag-hku` 直装方式部署 LightRAG（单进程），采用 Postgres 一体存储（KV + 向量 + 图状态单库，pgvector 扩展），无需 Docker/WSL2/ES/MinIO/Redis/MySQL 多组件。

#### Scenario: Win 原生部署
- **WHEN** 在 Win11 执行 `pip install lightrag-hku` 与 Postgres 初始化
- **THEN** LightRAG 单进程启动，Postgres 一体存储就绪，无 Docker/ES/MinIO/Redis/MySQL 组件依赖

#### Scenario: Postgres 一体存储验证
- **WHEN** LightRAG 完成实体/向量/图状态写入
- **THEN** KV（文档/缓存）、向量（pgvector）、图状态（节点/边）三域均落同一 Postgres 库，单库支撑全部检索

#### Scenario: 单进程资源占用
- **WHEN** LightRAG 运行索引与查询
- **THEN** 仅有单一 Python 进程，内存与连接数可控，无多容器编排开销

#### Scenario: 边缘 - PG 连接失败兜底
- **WHEN** Postgres 连接池耗尽或不可达
- **THEN** LightRAG 以指数退避重试并记录告警，超出阈值后熔断返回错误码而非静默丢数据

#### Scenario: 错误 - Docker 残留冲突
- **WHEN** 机器存在历史 RAGFlow Docker 容器占用端口/卷
- **THEN** 部署脚本能检测并提示清理，不因端口/卷冲突导致 LightRAG 启动失败

### Requirement: opencode 订阅 LLM 与嵌入对接
系统 SHALL 复用 opencode 已订阅大模型作为 LightRAG 的 LLM 与嵌入后端，通过 OpenAI 兼容端点（`LLM_BINDING_HOST` / `EMBEDDING_BINDING_HOST`）+ AK/SK 鉴权，并支持角色级模型切换：实体抽取用 `qwen2.5-coder`（省 token），根因推理用 `deepseek-v3`（强模型）；嵌入用 MaaS `bge-m3`（dim=1024）。

#### Scenario: 角色级模型切换
- **WHEN** LightRAG 执行实体/关系抽取
- **THEN** 使用 `EXTRACT_LLM_MODEL=qwen2.5-coder`；执行查询/根因推理时使用 `QUERY_LLM_MODEL=deepseek-v3`，由角色级配置注入对应 `llm_model_func`

#### Scenario: 嵌入维度校验
- **WHEN** LightRAG 写入向量
- **THEN** 向量维度恒为 `EMBEDDING_DIM=1024`（bge-m3），与 pgvector schema 维度一致，无维度不匹配报错

#### Scenario: 边缘 - 配额受限兜底
- **WHEN** opencode 订阅 LLM 配额受限或限流（429）
- **THEN** 对接封装按退避重试，阈值后降级到备用模型或返回部分结果并标注降级标记

#### Scenario: 错误 - AK/SK 鉴权失败
- **WHEN** `LLM_BINDING_API_KEY` 失效或越权
- **THEN** 初始化阶段即抛鉴权错误并中止，避免后续索引产生无效数据

#### Scenario: 边缘 - 端点不可达重试
- **WHEN** OpenAI 兼容端点瞬时不可达
- **THEN** 封装按指数退避重试 N 次，最终失败则上抛可读错误而非卡死

### Requirement: 三路检索分层路由
系统 SHALL 承载三类检索意图的分层路由：历史经验匹配走 low-level（实体检索）+ hybrid 模式；根因传播追溯走图谱遍历（含注入的 CodeGraph 调用边）；全局架构理解走 high-level（主题/社区摘要 + 代码中文大纲聚合）。

#### Scenario: 历史经验匹配
- **WHEN** A4 根因分析检索相似历史工单
- **THEN** 以 low-level 实体检索 + hybrid 模式从历史工单文档向量命中，返回 Top-K 相似工单

#### Scenario: 根因传播追溯
- **WHEN** 查询 "PaymentClient.charge 超时的根因传播路径"
- **THEN** 经 LightRAG 图谱遍历（含注入的 CodeGraph 调用边）返回传播路径，使用 `QueryParam(mode="hybrid")`

#### Scenario: 全局架构理解
- **WHEN** A5 方案生成需理解全局架构
- **THEN** 以 high-level 主题/社区摘要（代码中文大纲聚合）返回，使用 `QueryParam(mode="high_level")`

#### Scenario: 错误 - 模式参数非法
- **WHEN** `QueryParam.mode` 取值不在 `hybrid | low_level | high_level` 枚举内
- **THEN** 路由层校验失败并返回参数错误，不进入查询

#### Scenario: 边缘 - 空结果回退
- **WHEN** 三路检索均无命中
- **THEN** 路由层返回空结果并标注未命中，调用方可回退到默认策略而非崩溃

### Requirement: CodeGraph 调用图注入
系统 SHALL 将 CodeGraph 提取的真实调用关系（实体：函数符号 + 中文大纲 description；关系：calls 边 + weight）经 `ainsert_custom_kg` 注入 LightRAG 图谱，确保结构域为真实调用关系而非 LLM 幻觉抽取。

#### Scenario: 注入真实调用图（非幻觉）
- **WHEN** CodeGraph 构建完成
- **THEN** LightRAG 图谱存在实体 `func:OrderService.create`（description 为中文大纲 "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号"）与关系 `func:OrderService.create → func:PaymentClient.charge`（description=calls, weight=8.0）

#### Scenario: 调用边权重校验
- **WHEN** 注入 relationships
- **THEN** weight 字段为正浮点（如 8.0），反映调用强度，缺失时按默认权重填充而非报错

#### Scenario: 错误 - 幻觉实体拒绝注入
- **WHEN** 注入数据中实体缺少 type 或 description 为空
- **THEN** 注入适配层校验失败并跳过该条，记录告警，不写入残缺实体

#### Scenario: 边缘 - 重复注入幂等
- **WHEN** 同一调用图被重复 `ainsert_custom_kg`
- **THEN** 去重/更新而非产生重复边，保证图谱幂等

#### Scenario: 边缘 - 空调用图处理
- **WHEN** CodeGraph 输出为空（无函数调用关系）
- **THEN** 注入适配层跳过并记录，不抛异常阻断流程

### Requirement: 历史问题单文本索引
系统 SHALL 将历史问题单文档（根因、验证、代码片段）批量 `ainsert` 索引到 LightRAG 文本域，作为历史经验匹配数据源。

#### Scenario: 批量历史工单索引
- **WHEN** 知识库导入历史问题单
- **THEN** 批量文档经 `await rag.ainsert(docs)` 完成文本域索引

#### Scenario: 文本域可被 low-level 命中
- **WHEN** 以 low-level + hybrid 检索相似工单
- **THEN** 命中已索引历史工单，返回带原工单标识的结果

#### Scenario: 错误 - 空文档/格式非法
- **WHEN** 批量文档中存在空文档或字段缺失
- **THEN** 跳过非法条目并记录，不阻断整批索引

#### Scenario: 边缘 - 超大批量分页
- **WHEN** 历史工单数量超过单批阈值
- **THEN** 自动分页/分批 ainsert，控制单批 token 与内存

#### Scenario: 边缘 - 增量追加去重
- **WHEN** 二次导入重叠工单
- **THEN** 按文档标识去重/更新，避免重复索引膨胀

### Requirement: 重排与增量更新
系统 SHALL 默认开启重排（rerank），并支持增量更新（知识飞轮回写时仅增量更新受影响社区摘要，非全量重建）。

#### Scenario: 证据重排三档评估
- **WHEN** CRAG 闸门评估检索证据相关性
- **THEN** LightRAG 重排器对证据打分，输出相关/模糊/不相关三档

#### Scenario: 飞轮回写增量更新
- **WHEN** 知识飞轮回写新案例
- **THEN** 仅 `ainsert` 增量并刷新受影响社区摘要，未命中社区不重建

#### Scenario: 错误 - 重排器超时降级
- **WHEN** rerank 超时或异常
- **THEN** 降级返回未重排原始排序并标注，不阻断查询

#### Scenario: 边缘 - 社区摘要增量范围
- **WHEN** 新增文档涉及多个社区
- **THEN** 仅重算受影响社区摘要，计算量受控

#### Scenario: 边缘 - 重排阈值边界
- **WHEN** 证据分数落在分档阈值附近
- **THEN** 按既定阈值归档，边界值归属明确（如 ≥阈值=相关）

## 技术细节

### 接口定义
LightRAG API 签名（异步）：

```python
from lightrag import LightRAG, QueryParam

rag = LightRAG(
    working_dir="./rag",
    llm_model_func=opencode_llm,
    embedding_func=maas_bge_m3,
)

await rag.ainsert(docs)

await rag.ainsert_custom_kg(ast_kg)

result: str = await rag.aquery(
    query,
    param=QueryParam(mode="hybrid" | "low_level" | "high_level"),
)
```

### 数据结构
`ast_kg`（CodeGraph → LightRAG 注入）JSON schema：

```json
{
  "entities": [
    {
      "entity_name": "func:OrderService.create",
      "type": "function",
      "description": "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号"
    }
  ],
  "relationships": [
    {
      "src_id": "func:OrderService.create",
      "tgt_id": "func:PaymentClient.charge",
      "description": "calls",
      "weight": 8.0
    }
  ]
}
```

`QueryParam` schema：

```json
{
  "mode": "hybrid | low_level | high_level",
  "top_k": 60,
  "...": "其余 LightRAG 原生查询参数"
}
```

### 配置项
`.env`（对接 opencode 订阅）：

```env
LLM_BINDING=openai
LLM_MODEL=deepseek-v3
LLM_BINDING_HOST=https://<opencode>/v1
LLM_BINDING_API_KEY=<opencode AK/SK>

EMBEDDING_BINDING=openai
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024
EMBEDDING_BINDING_HOST=https://<MaaS>/v1

EXTRACT_LLM_MODEL=qwen2.5-coder
QUERY_LLM_MODEL=deepseek-v3
```

三路检索分层路由表：

| 检索意图 | LightRAG 模式 | 数据源 |
| --- | --- | --- |
| 历史经验匹配 | low-level（实体检索）+ hybrid | 历史工单文档向量 |
| 根因传播追溯 | 图谱遍历（含注入的 CodeGraph 调用边） | LightRAG 图 + CodeGraph 调用图 |
| 全局架构理解 | high-level（主题/社区摘要） | 代码中文大纲聚合 |

LightRAG vs RAGFlow 选型对比（弃用 RAGFlow 依据）：

| 维度 | LightRAG（采用） | RAGFlow（弃用） |
| --- | --- | --- |
| Win 原生部署 | ✓ pip 直装, 无需 Docker | 需 Docker + WSL2 + vm.max_map_count |
| 部署复杂度 | 极低（单进程） | 重（ES + MinIO + Redis + MySQL） |
| 存储 | Postgres 一体（KV + 向量 + 图） | 多组件 |
| 双层级检索 | ✓ low + high 原生 | 需配置 |
| 自定义 KG 注入 | ✓ insert_custom_kg | 部分 |
| 复用 opencode 订阅 | ✓ OpenAI 兼容端点 | ✓ |
| 重排 | ✓ 默认开启 | ✓ |
| 索引成本 | 低（~1/30 GraphRAG） | 低 |
| 增量更新 | ✓ | ✓ |

## 验收指标
- 索引成本 ≤ 1/30 GraphRAG（以同语料 GraphRAG 全量索引 LLM token 消耗为基准对比）。
- 三路检索命中率达标：历史经验匹配 Top-K 命中率达标；根因传播追溯路径召回达标；全局架构理解社区覆盖达标。
- 重排三档（相关/模糊/不相关）可用：分档阈值明确，边界样例归档正确率 100%。
- 增量更新生效：飞轮回写后仅受影响社区摘要重算，全量重建触发率为 0。
- Win 原生部署零 Docker 依赖，单进程启动，Postgres 一体库就绪。
- 角色级模型切换生效：抽取走 `qwen2.5-coder`、推理走 `deepseek-v3`、嵌入走 `bge-m3` dim=1024。
- 调用图注入为真实调用关系（非 LLM 幻觉）：`ainsert_custom_kg` 后图谱存在对应实体/关系，幂等不重复。

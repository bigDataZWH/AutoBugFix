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

## UT 测试方案

测试框架：Python `pytest` + `pytest-asyncio`；Postgres 用 `testcontainers` 或 `docker-compose` 起临时实例；embedding 用 mock（固定 1024 维向量）或本地 BGE-M3；LLM 用 mock（固定 JSON 抽取/查询响应）。每个用例标注 mock 策略与边界。

| # | 用例名 | 被测组件 | 输入 | 预期输出 | mock 策略 |
| --- | --- | --- | --- | --- | --- |
| 1 | test_ast_kg_schema_validation | CodeGraph→LightRAG 注入适配层（`ast_kg` schema 校验） | `ast_kg` JSON：含 entities（entity_name/type/description）、relationships（src_id/tgt_id/description/weight）；构造非法样例（缺 entities、type 非枚举、description 空、weight 非正、src/tgt 反向） | 合法 JSON 通过校验；非法样例各自抛 `SchemaValidationError` 并记录告警，不写入残缺实体；边方向缺失按默认填充 | 纯函数级 mock，无 DB/LLM 依赖；用 `pytest.mark.parametrize` 覆盖合法/非法矩阵 |
| 2 | test_lightrag_init | `LightRAG(working_dir, llm_model_func, embedding_func)` 初始化 | `working_dir=tempfile.mkdtemp()`、mock llm_model_func/embedding_func、Postgres 连接串 | 实例创建成功；`working_dir` 目录已创建；Postgres 连接池就绪；pgvector 扩展已启用；`embedding_dim==1024` | testcontainers 起 Postgres；mock llm/embedding 返回固定值；`tmp_path` fixture 隔离工作目录 |
| 3 | test_ainsert_async | `rag.ainsert(docs)` 异步插入管线 | docs：含中文大纲与历史工单文本（≥2 段） | 文本切片完成；实体抽取（mock 返回固定实体集）；关系抽取；社区聚类生成社区摘要；KV/向量/图三域均写入 Postgres | mock `opencode_llm` 返回固定抽取 JSON；mock embedding 返回 1024 维向量；testcontainers Postgres；断言 DB 表记录数 |
| 4 | test_ainsert_custom_kg | `rag.ainsert_custom_kg(ast_kg)` 注入 | `ast_kg` JSON（func 实体 + calls 边，weight=8.0） | 实体/边写入 Postgres 图状态表；description=中文大纲；weight=8.0；重复注入幂等不重复；空 ast_kg 不阻断 | testcontainers Postgres；mock embedding 1024 维；无需 mock LLM（注入不经 LLM 抽取）；断言实体表/边表记录 |
| 5 | test_aquery_naive | `rag.aquery(query, param=QueryParam(mode="low_level"))` | 关键词 query（如 "OrderService.create"） | 返回低级实体检索结果；命中已注入/已索引实体；结果含实体 description | mock embedding（query 向量 1024 维）；testcontainers Postgres（预置实体）；mock LLM 查询响应；断言结果非空且含目标实体 |
| 6 | test_aquery_local | `rag.aquery(mode="local")` 局部子图检索 | query 触发局部子图（含调用边） | 返回局部子图实体+关系；包含注入的 calls 边 | testcontainers Postgres（预置 ast_kg 子图）；mock embedding/LLM；断言返回含 `func:OrderService.create→func:PaymentClient.charge` |
| 7 | test_aquery_hybrid | `rag.aquery(mode="hybrid")` 双层融合 | query 需双层命中（历史工单 + 主题社区） | low-level 实体与 high-level 社区摘要融合结果；经重排后返回；结果按重排分数排序 | testcontainers Postgres（预置文档+社区）；mock LLM/embedding；spy 重排器调用；断言双层结果合并且排序正确 |
| 8 | test_query_param_config | `QueryParam` 配置构造与校验 | QueryParam(mode/top_k/similarity_threshold/only_need_context) 各取值组合；含非法 mode | 合法 mode（hybrid/low_level/high_level）通过；非法 mode 抛参数错误；top_k/similarity_threshold 边界（0/负数/超大）归档正确 | 纯契约测试，无 DB/LLM；`pytest.mark.parametrize` 覆盖合法/非法/边界 |
| 9 | test_embedding_dim | `maas_bge_m3` 嵌入函数 | 文本输入（中/英） | 返回向量 `len(vec)==1024`；与 pgvector schema 维度一致；空文本不报错 | mock MaaS 端点返回固定 1024 维向量；或本地 BGE-M3 实测；断言维度恒为 1024 |
| 10 | test_postgres_storage | Postgres 存储层（实体表/边表/社区表 CRUD） | 实体/边/社区记录的增删改查操作 | 实体表 upsert 幂等；边表 weight 更新；社区表摘要增量刷新；CRUD 返回受影响行数正确 | testcontainers Postgres；直接调用存储层 DAO；mock 上游 LLM；断言三表 CRUD 行为与受影响行数 |
| 11 | test_retrieval_routing | 检索路由层（意图→mode 分派） | 三类查询意图（历史经验匹配/根因传播追溯/全局架构理解） | 分别路由到 hybrid/图谱遍历/high_level；非法意图回退默认或报错；空结果有回退标注 | mock 路由决策函数；mock 下游 aquery；`parametrize` 三意图；断言 mode 分派正确 |
| 12 | test_reranker_triage | 重排序三档分类 | 证据集（分数跨相关/模糊/不相关三档，含阈值边界样例） | 三分类正确归档；边界值（=阈值）归"相关"；重排器超时降级返回未重排排序+标注 | mock 重排器打分函数；构造边界分数；mock 超时异常；断言三档归档正确率 100% 与降级标注 |

UT 覆盖率目标：line + branch ≥ 85%。CI 流水线集成 `pytest --cov` 执行。

## E2E 测试方案

测试环境：`testcontainers` 起 Postgres（pgvector）；embedding 用本地 BGE-M3 或 mock 1024 维；LLM 用 mock 或 opencode 订阅端点（按 CI 标记区分）。每个场景为端到端全链路。

### 场景 1：e2e_full_retrieval_flow — 完整检索流程
- **前置条件**：code2cn 已输出中文大纲；Postgres 一体库就绪；embedding/LLM 可用
- **测试步骤**：
  1. 将 code2cn 输出封装为 `ast_kg` JSON
  2. `await rag.ainsert_custom_kg(ast_kg)` 注入结构域
  3. `await rag.aquery(query, param=QueryParam(mode="hybrid"))` 查询
  4. 对返回结果做排序校验
- **预期结果**：注入实体可被检索命中；结果按相关性排序返回
- **断言点**：注入实体名出现在结果；结果非空；排序分数单调递减；Postgres 图谱表存在对应实体/边

### 场景 2：e2e_hybrid_query — hybrid 双层融合检索
- **前置条件**：文本域已 aindex（历史工单）+ 结构域已注入 ast_kg（社区摘要已生成）
- **测试步骤**：
  1. `QueryParam(mode="hybrid")` 发起查询
  2. 分别采集 low-level 实体结果与 high-level 社区摘要结果
  3. 校验融合后结果集
- **预期结果**：low-level + high-level 结果合并并重排
- **断言点**：返回结果同时含实体级与社区级条目；融合后去重；重排分数顺序正确；双层均有命中

### 场景 3：e2e_custom_kg_inject — ast_kg 注入全链路
- **前置条件**：CodeGraph CPG 已构建；generate-code-chinese-outline 中文大纲已生成
- **测试步骤**：
  1. CodeGraph CPG → 转换为 `ast_kg` JSON（entities + relationships）
  2. `await rag.ainsert_custom_kg(ast_kg)` 注入
  3. 以实体名查询验证可检索
- **预期结果**：注入的调用关系可被检索
- **断言点**：`func:OrderService.create→func:PaymentClient.charge`（calls, weight=8.0）可查；description=中文大纲；重复注入幂等不重复；注入实体可追溯到 CodeGraph 真实调用（非幻觉）

### 场景 4：e2e_chinese_query_retrieval — 中文根因查询检索
- **前置条件**：中文大纲已注入；中文 query 语料就绪
- **测试步骤**：
  1. 发起中文 query（如 "PaymentClient.charge 超时的根因传播路径"）
  2. `QueryParam(mode="hybrid")` 检索
- **预期结果**：中文 query 命中中文大纲实体
- **断言点**：返回结果含中文大纲 description 实体；根因传播路径包含注入调用边；中文 token 正确编码无乱码

### 场景 5：e2e_incremental_insert — 增量插入
- **前置条件**：已有初始索引与社区摘要；记录初始社区摘要版本
- **测试步骤**：
  1. 新增函数大纲（新 `ast_kg` 片段）
  2. `await rag.ainsert_custom_kg(new_ast_kg)` 增量注入
  3. 校验受影响社区摘要刷新
- **预期结果**：仅受影响社区摘要更新
- **断言点**：新实体可检索；受影响社区摘要版本/内容更新；未受影响社区摘要不变（hash 一致）；全量重建触发率为 0

### 场景 6：e2e_reranker_gate — 重排序闸门
- **前置条件**：检索结果可分级（构造 relevant/ambiguous/irrelevant 三类证据）
- **测试步骤**：
  1. 触发检索获取候选证据
  2. 重排器分档：relevant 直通 / ambiguous 触发补充检索 / irrelevant 拒绝
  3. 分别校验三档行为
- **预期结果**：三档分流正确
- **断言点**：relevant 直通返回结果；ambiguous 触发补充检索（二次检索调用被记录）；irrelevant 被拒绝并标注；重排器超时降级返回未重排排序+标注

## 跨模块集成测试方案

集成测试聚焦 LightRAG 检索引擎与上下游模块的跨契约集成，验证数据在模块边界处的字段映射、注入语义与检索/重排衔接正确。区别于 UT（单组件）与 E2E（用户视角全链路），集成测试以「跨模块契约点」为单元，每场景明确涉及模块、集成点、测试步骤、预期结果与断言点。

### 上下游依赖关系表

| 方向 | 模块 | 契约载体 | LightRAG 侧入口/出口 | 集成点说明 |
| --- | --- | --- | --- | --- |
| 上游 | generate-code-chinese-outline（CodeOutline） | CodeOutline JSON 实体描述 | `ainsert_custom_kg` entities[].description | 代码中文大纲映射为实体 description 字段 |
| 上游 | build-codegraph-knowledge-graph（CodeGraph） | ast_kg JSON（CPG 实体/边） | `ainsert_custom_kg(ast_kg)` | CPG 实体/边 → LightRAG 实体/边（非 LLM 抽取） |
| 上游 | build-dual-gate-flywheel（知识飞轮回写） | flywheel writeback 向量化实体 | `ainsert` 增量 + SIMILAR_TO 边 | 回写实体建 SIMILAR_TO 边、受影响社区增量刷新 |
| 下游 | orchestrate-five-agent-engine（A4 根因分析） | aquery 检索请求 | `aquery(query, QueryParam(mode="hybrid"))` | A4 取根因候选，QueryParam hybrid 模式 |
| 下游 | build-dual-gate-flywheel（双闸门 CRAG） | 检索结果集 | rerank 三分类 | relevant/ambiguous/irrelevant 三档分流 |

### 集成测试场景

#### 场景 integ_code2cn_to_lightrag_ainsert — CodeOutline JSON → ainsert_custom_kg 注入（实体描述字段映射）
- **涉及模块**：generate-code-chinese-outline → LightRAG
- **集成点**：CodeOutline JSON 实体描述 → `ainsert_custom_kg` entities[].description 字段映射
- **测试步骤**：
  1. 构造 CodeOutline JSON（含实体符号、中文大纲描述、定位信息）
  2. 经映射适配层转换为 ast_kg JSON（description 取中文大纲原文）
  3. `await rag.ainsert_custom_kg(ast_kg)` 注入
  4. 以实体名检索校验 description 落库
- **预期结果**：中文大纲映射为实体 description 并入库；实体可被检索命中
- **断言点**：注入实体 `description == CodeOutline 中文大纲原文`；字段无截断/乱码；Postgres 实体表 description 列与 CodeOutline 一致；无幻觉实体混入

#### 场景 integ_codegraph_astkg_to_lightrag — ast_kg JSON → ainsert_custom_kg（CPG 实体/边 → LightRAG 实体/边）
- **涉及模块**：build-codegraph-knowledge-graph → LightRAG
- **集成点**：ast_kg JSON（entities/edges）→ `ainsert_custom_kg` 写入 LightRAG 图状态表
- **测试步骤**：
  1. CodeGraph 构建完成，产出 ast_kg JSON（func 实体 + calls 边，weight=8.0）
  2. `await rag.ainsert_custom_kg(ast_kg)` 注入结构域
  3. 遍历 Postgres 实体表/边表核对落库内容
  4. 重复注入验证幂等
- **预期结果**：CPG 实体/边正确落库为 LightRAG 实体/边；调用关系为真实调用而非幻觉
- **断言点**：实体表存在 `func:OrderService.create`、`func:PaymentClient.charge`；边表存在 calls 边且 weight=8.0；重复注入后边表无重复记录；注入实体可追溯到 CodeGraph CPG 原始节点

#### 场景 integ_flywheel_to_lightrag_ainsert — 飞轮回写实体 → ainsert → SIMILAR_TO 边创建
- **涉及模块**：build-dual-gate-flywheel → LightRAG
- **集成点**：flywheel writeback 向量化实体 → `ainsert` 增量 + SIMILAR_TO 边
- **测试步骤**：
  1. 模拟飞轮回写新案例（root_cause/function/path/patch/case 字段）
  2. `await rag.ainsert(回写实体文本)` 增量注入
  3. 触发实体级相似度计算，校验 SIMILAR_TO 边创建
  4. 校验受影响社区摘要增量刷新
- **预期结果**：回写实体入库；与新案例相似的已有实体间建立 SIMILAR_TO 边；仅受影响社区摘要刷新
- **断言点**：回写实体可检索；边表存在 SIMILAR_TO 边且两端实体均存在；相似度 ≥ similarity_threshold；未受影响社区摘要 hash 不变（全量重建触发率=0）

#### 场景 integ_lightrag_to_agent4_aquery — A4 通过 aquery 检索根因候选（QueryParam hybrid 模式）
- **涉及模块**：LightRAG → orchestrate-five-agent-engine（A4）
- **集成点**：A4 根因分析 → `aquery(query, QueryParam(mode="hybrid"))` 检索根因候选
- **测试步骤**：
  1. 预置根因候选实体/案例到 LightRAG（含 ast_kg 调用边 + 历史工单文本）
  2. A4 以根因 query（如 "PaymentClient.charge 超时的根因传播路径"）调用 `aquery`
  3. 校验 `QueryParam(mode="hybrid", top_k=60, similarity_threshold=...)` 传参与契约
  4. 校验返回根因候选集
- **预期结果**：A4 经 hybrid 检索获得根因候选，含实体级与社区级融合结果
- **断言点**：返回结果非空且含目标根因实体；mode="hybrid" 生效；top_k 截断生效；结果含根因传播路径（调用边）；QueryParam 字段经契约校验通过

#### 场景 integ_lightrag_to_crag_reranker — 检索结果 → CRAG 重排序三分类（relevant/ambiguous/irrelevant）
- **涉及模块**：LightRAG → build-dual-gate-flywheel（双闸门 CRAG）
- **集成点**：LightRAG 检索结果集 → CRAG reranker 三分类
- **测试步骤**：
  1. 触发检索获取候选证据集（含高/中/低相关性样本与阈值边界样例）
  2. 将结果集传入 CRAG reranker
  3. 校验三分类：relevant 直通 / ambiguous 触发补充检索 / irrelevant 拒绝
  4. 校验重排器超时降级路径
- **预期结果**：证据按三档分流；边界样例归档正确
- **断言点**：relevant 集直通返回；ambiguous 集触发二次检索调用记录；irrelevant 集被拒并标注；边界分数（=阈值）归 relevant；超时降级返回未重排原始排序+标注

#### 场景 integ_full_pipeline_lightrag — code2cn + ast_kg 注入 → aquery 检索 → CRAG 重排序全链路
- **涉及模块**：generate-code-chinese-outline + build-codegraph-knowledge-graph → LightRAG → A4 + CRAG
- **集成点**：上游注入（code2cn + ast_kg）→ `ainsert_custom_kg` → `aquery` → CRAG reranker 全链路衔接
- **测试步骤**：
  1. CodeOutline 中文大纲 + CodeGraph ast_kg 经适配层合并后 `ainsert_custom_kg` 注入
  2. A4 发起 hybrid `aquery` 检索根因候选
  3. 检索结果传入 CRAG reranker 三分类
  4. 校验端到端链路各环节衔接与数据透传
- **预期结果**：全链路贯通，注入的中文大纲/调用边经检索后由 CRAG 正确分档
- **断言点**：注入实体出现在 aquery 命中结果；命中结果经 CRAG 分类后 relevant 集含目标根因；ambiguous 触发补充检索后命中提升；各环节无数据丢失/字段错位；全链路无幻觉实体

## 测试数据与 Mock 规范

### 测试数据构造策略

- **Postgres testcontainers**：集成测试/E2E 均用 `testcontainers-python` 起临时 Postgres（含 pgvector 扩展），真实执行 SQL（不 mock DB 层），保证 schema/约束/向量索引行为与生产一致；会话级 fixture 启停，测试结束自动销毁。
- **实体/边/社区 Fixture 工厂**：以工厂函数（factory pattern）按参数生成实体/边/社区记录，避免散落硬编码；工厂返回结构化 dict/dataclass，支持批量生成与字段覆盖（override）。
- **conftest.py 集中注册**：所有 fixture（Postgres 容器、LightRAG 实例、mock llm/embedding、ast_kg 样本加载、CRAG reranker mock）在 `conftest.py` 统一注册，作用域 session/function 级可控；样本 JSON 经 fixture 按需加载。
- **数据隔离**：每个用例用独立 schema/库或事务回滚隔离，避免用例间数据污染；`working_dir` 用 `tmp_path` 隔离。

### Mock 数据样本

#### ast_kg JSON 样本（entities 数组 + edges 数组）

```json
{
  "entities": [
    {"entity_name": "func:OrderService.create", "entity_type": "function", "description": "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号"},
    {"entity_name": "func:PaymentClient.charge", "entity_type": "function", "description": "支付扣款:调用支付网关→等待应答→记录流水"},
    {"entity_name": "class:OrderService", "entity_type": "class", "description": "订单领域服务:聚合创建/查询/取消"}
  ],
  "edges": [
    {"src_id": "func:OrderService.create", "tgt_id": "func:PaymentClient.charge", "relation_type": "calls", "description": "同步调用支付扣款", "weight": 8.0},
    {"src_id": "class:OrderService", "tgt_id": "func:OrderService.create", "relation_type": "contains", "description": "类包含方法", "weight": 5.0}
  ]
}
```

枚举值约定：`entity_type ∈ {function, class, module, variable}`；`relation_type ∈ {calls, contains, imports, similar_to, depends_on}`。

#### CodeOutline → LightRAG 实体描述映射样本

```json
{
  "code_outline": {
    "symbol": "OrderService.create",
    "kind": "method",
    "file": "src/order/service.py",
    "line_range": [42, 78],
    "chinese_outline": "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号"
  },
  "lightrag_entity": {
    "entity_name": "func:OrderService.create",
    "entity_type": "function",
    "description": "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号"
  }
}
```

映射规则：`symbol` → `entity_name`（按 kind 加 `func:`/`class:` 前缀）；`chinese_outline` → `description` 原样透传，无截断。

#### aquery 请求/响应 Mock（naive/local/hybrid 三种模式响应）

请求：

```json
{
  "query": "PaymentClient.charge 超时的根因传播路径",
  "param": {"mode": "hybrid", "top_k": 60, "similarity_threshold": 0.35, "only_need_context": false}
}
```

naive（low_level）响应：

```json
{
  "mode": "low_level",
  "results": [
    {"entity": "func:PaymentClient.charge", "description": "支付扣款:调用支付网关→等待应答→记录流水", "score": 0.92}
  ]
}
```

local 响应（局部子图）：

```json
{
  "mode": "local",
  "results": [
    {"entity": "func:OrderService.create", "description": "创建订单:校验参数→查库存→写订单表→发MQ→返回订单号", "edges": [{"tgt": "func:PaymentClient.charge", "relation_type": "calls", "weight": 8.0}]}
  ]
}
```

hybrid 响应（双层融合）：

```json
{
  "mode": "hybrid",
  "results": [
    {"entity": "func:PaymentClient.charge", "description": "支付扣款:调用支付网关→等待应答→记录流水", "score": 0.91, "level": "low"},
    {"community": "支付域根因社区", "summary": "PaymentClient.charge 超时多因网关限流/连接池耗尽", "score": 0.88, "level": "high"}
  ]
}
```

#### QueryParam 配置样本 JSON

```json
{
  "mode": "hybrid",
  "top_k": 60,
  "similarity_threshold": 0.35,
  "only_need_context": false,
  "stream": false
}
```

合法约束：`mode ∈ {hybrid, low_level, high_level}`；`top_k` 正整数；`similarity_threshold ∈ [0,1]`。

#### BGE-M3 embedding Mock（dim=1024 向量数组）

```json
{
  "model": "bge-m3",
  "dim": 1024,
  "vector": [0.0123, -0.0456, 0.0789, "...(共 1024 个浮点)", 0.0321]
}
```

Mock 规则：按输入文本 hash 生成确定性 1024 维向量（同输入同输出），首 3 维用于断言锚点，其余按规则填充；`len(vector) == 1024` 恒成立，与 pgvector schema 维度一致。

#### CRAG 重排序三分类响应 Mock（relevant/ambiguous/irrelevant）

```json
{
  "triage": {
    "relevant": [
      {"entity": "func:PaymentClient.charge", "score": 0.91, "action": "pass"}
    ],
    "ambiguous": [
      {"entity": "func:OrderService.create", "score": 0.48, "action": "refetch"}
    ],
    "irrelevant": [
      {"entity": "class:Logger", "score": 0.12, "action": "reject"}
    ]
  },
  "degraded": false
}
```

边界约定：`score ≥ 0.8` → relevant；`0.35 ≤ score < 0.8` → ambiguous；`score < 0.35` → irrelevant；`score == 阈值` 归 relevant。

#### 飞轮回写实体 Mock（root_cause/function/path/patch/case）

```json
{
  "case_id": "CASE-2026-0831-001",
  "root_cause": "PaymentClient.charge 连接池耗尽导致超时",
  "function": "func:PaymentClient.charge",
  "path": "src/payment/client.py:88-120",
  "patch": "diff --git a/src/payment/client.py @@ -88 +88 @@ -timeout=5 +timeout=30",
  "case": "高并发下单触发支付超时，扩容连接池后恢复"
}
```

### Mock 规范

- **Postgres 用 testcontainers（不 mock，真实 SQL）**：DB 层走真实 Postgres + pgvector，验证 schema/约束/向量索引/事务/upsert 幂等真实行为；禁止用内存 sqlite 或 mock DAO 替代。
- **BGE-M3 embedding mock**：返回固定 dim=1024 向量，不加载真实模型；按输入文本生成确定性向量（同输入同输出）保证可复现；向量维度恒为 1024，与 pgvector schema 维度一致。
- **LightRAG 内部 LLM 抽取 mock**：实体/关系抽取返回预设 JSON 结果（fixture 加载），不调用真实 opencode 订阅端点；区分抽取角色（EXTRACT_LLM_MODEL=qwen2.5-coder）与查询角色（QUERY_LLM_MODEL=deepseek-v3）mock，验证角色级注入。
- **CRAG reranker mock**：三分类返回预设标签（relevant/ambiguous/irrelevant），按预设分数阈值归档；提供超时/异常分支 mock 验证降级路径。

### 测试数据库初始化

1. `testcontainers` 启动 Postgres 容器（镜像含 pgvector 扩展）。
2. 执行 LightRAG schema DDL：创建实体表、边表、社区表、KV/缓存表、向量列（`vector(1024)`）、向量索引（HNSW/IVFFlat）。
3. 测试数据 seed：加载 ast_kg 样本实体/边、历史工单文档、社区摘要到对应表；记录初始行数与社区摘要 hash 作为断言基线。
4. 会话结束自动销毁容器（`docker rm -f`），不残留。
5. DDL 与 seed 脚本置于 `tests/fixtures/lightrag/` 下，由 fixture 自动执行。

### Fixture 文件组织

```
tests/fixtures/lightrag/
├── ast_kg/                          # 知识图谱样本
│   ├── order_payment_calls.json
│   └── inventory_deduct_contains.json
├── code_outline_map.json            # CodeOutline → LightRAG 实体描述映射样本
├── aquery_request.json              # aquery 请求样本
├── aquery_response_naive.json       # naive(low_level) 响应 Mock
├── aquery_response_local.json       # local 子图响应 Mock
├── aquery_response_hybrid.json      # hybrid 双层响应 Mock
├── query_param.json                 # QueryParam 配置样本
├── embedding_bge_m3.json            # BGE-M3 embedding Mock（dim=1024）
├── crag_reranker_triage.json        # CRAG 重排序三分类响应 Mock
└── flywheel_writeback.json          # 飞轮回写实体 Mock
```

约定：所有样本为纯静态 JSON（可版本化、可 diff）；工厂函数从 `conftest.py` 注入样本路径，按需加载；新增样本沿用上述命名与目录约定。

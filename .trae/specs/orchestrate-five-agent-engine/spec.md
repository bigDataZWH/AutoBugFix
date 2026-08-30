# 5-Agent 智能引擎 Spec

## Why
根因分析需要"症状理解→代码分析→链路分析→根因确认→方案生成"五角色分工协作，单 Agent 无法覆盖四维（代码/链路/指标/变更）关联。采用 LangGraph 状态机编排，A2/A3 并行 fan-out、A4 fan-in 聚合做四维关联，每阶段产物可独立审计与回放，配合双闸门与知识飞轮形成可自演化的根因定位闭环。

## What Changes
- **BREAKING** 新增 LangGraph 状态机编排入口 `run(RCAState)`：拓扑为 START→A1→(A2∥A3 fan-out 并行)→A4 fan-in 聚合→CRAG 门→HIL 门→A5→END，取代现有单线性阻塞式 RCA 流水线。
- **BREAKING** 新增对外 HTTP 接口 `POST /api/v1/rca/analyze`（输入 repo/branch/bug_link/bug_desc，输出 task_id）与 SSE 流式阶段推送 `GET /api/v1/rca/{task_id}/stream`，原有同步阻塞式 RCA 接口废弃。
- 新增 A1 问题理解 Agent：拉云捷 Bug 单→抽取症状/报错→生成检索 Query→定位嫌疑服务（云捷 Adapter + LLM 抽取）。
- 新增 A2 代码分析 Agent：opencode 拉码→CodeGraph 构建图谱→生成函数中文大纲→沿报错栈静态污点追踪→输出静态嫌疑函数集 S_static（opencode-codegraph + 中文大纲）。
- 新增 A3 链路分析 Agent：按时间窗拉 Trace→重建 span 树→识别异常传播路径→输出运行时异常路径 P_runtime（Trace Adapter + 异常检测）。
- 新增 A4 根因分析 Agent：四维关联交叉验证（静态∩运行时）→剪枝排序→输出 Top-3 根因，每条含 root_cause/confidence/evidence_chain/located_function。
- 新增 A5 方案生成 Agent：检索历史修复+最佳实践→生成补丁建议+验证用例（LightRAG high-level）。
- 新增根因 5 步标准化算法：定位范围→断言异常→挖掘关联→剪枝排序→输出 Top-3，并产出 5 段标准化报告（症状确认/链路分析/代码定位/根因确认/修复方案）。
- 新增 Celery 异步调度与 Redis 状态持久化（RCAState 序列化存盘），支持断点续跑与 HIL 挂起/回灌。

## Impact
- Affected specs: build-codegraph-knowledge-graph（A2 依赖）、setup-lightrag-retrieval-engine（A4/A5 依赖）、implement-dual-graph-validation（A4 交叉验证核心）、build-dual-gate-flywheel（CRAG/HIL 门位于 A4→A5 之间）、generate-code-chinese-outline（A2 大纲）、deploy-win11-local（编排层部署）
- Affected code: LangGraph 状态机定义（`orchestrator/state_machine.py`）、A1–A5 Agent 实现（`agents/a1_understand.py` … `agents/a5_solution.py`）、云捷/Trace Adapter（`adapters/yunjie.py`、`adapters/trace.py`）、Celery 任务调度（`tasks/rca_pipeline.py`）、RCAState 数据结构（`models/rca_state.py`）、SSE 网关（`api/rca_router.py`）

## ADDED Requirements

### Requirement: LangGraph 状态机编排
The system SHALL 使用 LangGraph 状态机编排 5-Agent 流水线，拓扑为 START→A1→(A2∥A3 fan-out 并行)→A4 fan-in 聚合→CRAG 门→HIL 门→A5→END，使用 Celery 异步调度与 Redis 状态持久化支持断点续跑。

#### Scenario: A2/A3 并行 fan-out
- **WHEN** A1 输出嫌疑服务且无上游错误
- **THEN** 调度器同时派发 A2 与 A3 两个分支；二者并行执行互不阻塞，各自独立产出

#### Scenario: A4 fan-in 聚合
- **WHEN** A2 与 A3 均完成并产出 S_static 与 P_runtime
- **THEN** A4 fan-in 节点等待两分支汇聚后做四维关联交叉验证，生成 Top-3

#### Scenario: 断点续跑恢复
- **WHEN** 流水线在 A2/A3/A4 任一阶段因进程崩溃或重启中断
- **THEN** 重启后从 Redis 反序列化 RCAState，比对 `stage` 字段，跳过已完成阶段，从断点阶段继续执行，无需重跑

#### Scenario: 配额受限兜底
- **WHEN** LLM/Trace/CodeGraph 任一上游配额耗尽或限流
- **THEN** 引擎进入降级模式，跳过该维度并标注 `degraded_dimensions`，仍以可用维度产出 Top-3 并在报告中标明降级标记

#### Scenario: 整体编排异常
- **WHEN** 状态机运行抛出未捕获异常
- **THEN** 捕获异常并将 RCAState.stage 置为 FAILED，写入错误堆栈到 Redis，SSE 推送 `error` 事件，task 状态置为 failed

### Requirement: A1 问题理解 Agent
The system SHALL 拉取云捷 Bug 单，抽取症状/报错，生成检索 Query，定位嫌疑服务。

#### Scenario: 症状抽取与嫌疑定位
- **WHEN** 输入 Bug 单链接/描述
- **THEN** 经云捷 Adapter 拉单 + LLM 抽取，输出症状列表、报错类型、检索 Query、嫌疑服务列表

#### Scenario: Bug 单拉取失败
- **WHEN** 云捷 Adapter 鉴权失败或 Bug 单链接 404
- **THEN** 回退使用 bug_desc 文本抽取症状；若 bug_desc 亦为空则 task 置为 failed 并返回 `A1_BUG_FETCH_ERROR`

#### Scenario: 嫌疑服务为空
- **WHEN** LLM 无法从症状定位到任何嫌疑服务
- **THEN** 扩大召回（按报错栈包名/service 名模糊匹配 CMDB），仍为空则将全量服务标记为嫌疑并降权，继续进入 A2/A3

#### Scenario: 报错栈缺失
- **WHEN** Bug 单中无 stack trace
- **THEN** 仅基于症状文本生成 Query 与嫌疑服务，并在 RCAState 标注 `error_type=unknown`，A2 静态追踪以入口函数为根

### Requirement: A2 代码分析 Agent
The system SHALL 经 opencode 拉码、CodeGraph 自动构建代码图谱、生成函数中文大纲，沿报错栈做静态污点追踪，输出静态嫌疑函数集 S_static。

#### Scenario: 静态污点追踪
- **WHEN** A1 给出报错栈与嫌疑服务
- **THEN** A2 在 CodeGraph 调用图上以报错栈帧为起点反向 BFS，输出 S_static（含嫌疑函数 id、函数名、调用路径、static_depth）

#### Scenario: CodeGraph 构建失败降级
- **WHEN** CodeGraph 构建超时或 opencode 拉码失败
- **THEN** 降级为基于 ripgrep 的正则定位，输出仅含函数名的 S_static（无调用路径），并在报告标注 `A2_GRAPH_DEGRADED`

#### Scenario: 报错栈帧无法映射函数
- **WHEN** 报错栈帧在 CodeGraph 中无对应节点
- **THEN** 沿栈帧上层寻找最近可达函数作为替代起点，并将替代信息写入 evidence_chain

#### Scenario: 嫌疑服务代码为空仓库
- **WHEN** opencode 拉码后仓库为空或分支不存在
- **THEN** S_static 置空并标注 `A2_REPO_EMPTY`，A4 仅基于 P_runtime 三维产出 Top-3

### Requirement: A3 链路分析 Agent
The system SHALL 按时间窗拉取 Trace，重建 span 树，识别异常传播路径，输出运行时异常路径 P_runtime。

#### Scenario: 异常传播路径识别
- **WHEN** A1 给出时间窗与嫌疑服务
- **THEN** A3 经 Trace Adapter 拉链路，重建 span 树，识别异常 span，输出异常传播路径 P_runtime（映射到函数）

#### Scenario: Trace 拉取失败
- **WHEN** Trace Adapter 不可用或时间窗内无链路数据
- **THEN** P_runtime 置空，A4 仅基于 S_static + Metric + 变更三维产出 Top-3，报告标注 `A3_TRACE_MISSING`

#### Scenario: span 无法映射函数
- **WHEN** Trace span 的 rpc endpoint 无法映射到代码函数
- **THEN** 保留 span 但 located_function 置空，由 A4 在交叉验证时降权处理

### Requirement: A4 根因分析 Agent
The system SHALL 对 S_static 与 P_runtime 做四维关联交叉验证（静态∩运行时），剪枝排序后输出 Top-3 根因，每条含根因描述、置信度、证据链、定位函数。

#### Scenario: 四维关联与 Top-3 输出
- **WHEN** A2 输出 S_static、A3 输出 P_runtime
- **THEN** A4 计算 `score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency`，按 score 降序剪枝保留 Top-3

#### Scenario: 四维关联交叉验证
- **WHEN** 某函数同时出现在 S_static 与 P_runtime.functions 中
- **THEN** 该函数置信度叠加 boost（静态可达且运行时异常），置为高优候选；仅命中一维的函数置信度衰减

#### Scenario: 低置信触发 HIL
- **WHEN** Top-3 中最高置信度 < τ（默认 0.6）
- **THEN** 引擎在 CRAG 门后挂起任务，gate_status 置为 HIL_PENDING，SSE 推送 `gate_pending` 事件，等待人工确认/修正/驳回后回灌 A4 重算

#### Scenario: 候选集为空
- **WHEN** 四维交集为空且降权后仍无候选
- **THEN** 输出 `top3=[]` 并标注 `A4_NO_CANDIDATE`，强制触发 HIL 由人工补充线索

#### Scenario: 候选数量不足 3
- **WHEN** 交叉验证后候选函数少于 3 个
- **THEN** 以全部候选输出，不足部分在 Top-3 中以 `{"root_cause": "insufficient_evidence", "confidence": 0}` 占位

### Requirement: A5 方案生成 Agent
The system SHALL 经 LightRAG high-level 检索历史修复与最佳实践，生成补丁建议与验证用例。

#### Scenario: 方案生成
- **WHEN** A4 输出 Top-3 根因并通过双闸门
- **THEN** A5 经 LightRAG high-level 检索，输出补丁建议、历史相似案例、验证用例

#### Scenario: LightRAG 检索为空
- **WHEN** LightRAG 未检索到相似历史案例
- **THEN** A5 仅基于 Top-3 根因生成补丁建议与回归用例，historical_cases 置空并标注 `A5_NO_HISTORY`

#### Scenario: 方案生成超时
- **WHEN** A5 生成耗时超过阈值
- **THEN** 返回已生成的部分方案，标注 `A5_PARTIAL`，允许人工后续补全

### Requirement: 双闸门机制
The system SHALL 在 A4→A5 之间接入 CRAG 自动纠偏门与 HIL 人工确认门，CRAG 评估证据相关性三档处理，HIL 在低置信时挂起并回灌 A4。

#### Scenario: CRAG 纠偏
- **WHEN** CRAG 重排器评估证据相关性为"模糊"
- **THEN** 触发补检（补充检索）；相关性"不相关"则改写 Query 重检索后回灌 A4；相关性"相关"则精炼后放行

#### Scenario: HIL 挂起/回灌 A4
- **WHEN** HIL 门被触发（低置信或 CRAG 改写后）
- **THEN** 任务挂起，gate_status=HIL_PENDING，前端推送确认面板；人工确认/修正/驳回后回灌 A4 重算 Top-3

#### Scenario: HIL 驳回终止
- **WHEN** 人工选择驳回
- **THEN** task 状态置为 rejected，流水线终止，记录驳回原因入知识飞轮

### Requirement: 根因 5 步标准化算法
The system SHALL 实现根因 5 步标准化定位流程：定位范围→断言异常→挖掘关联→剪枝排序→输出 Top-3，并产出 5 段标准化报告。

#### Scenario: 5 步流程产出
- **WHEN** 根因分析完成
- **THEN** 报告包含症状确认、链路分析、代码定位、根因确认、修复方案五段标准化产物

#### Scenario: 5 步标准化产出降级
- **WHEN** 任一步骤产物缺失（如 A3 失败导致链路分析段为空）
- **THEN** 该段标注 `step_degraded` 并附降级原因，仍保证 5 段结构完整输出

### Requirement: SSE 流式阶段推送
The system SHALL 通过 SSE 流式推送流水线阶段事件，支持断连重连续传。

#### Scenario: 阶段推送
- **WHEN** 流水线进入 A1/A2/A3/A4/A5 任一阶段
- **THEN** SSE 推送 `stage_start` 与 `stage_complete` 事件，携带阶段名与产物摘要

#### Scenario: 断连重连续传
- **WHEN** 前端 SSE 连接中断后重连
- **THEN** 客户端携带 `Last-Event-ID`，服务端从 Redis 事件流补发缺失事件，不丢阶段

## 技术细节

### 接口定义

```http
POST /api/v1/rca/analyze
Content-Type: application/json

Request:
{
  "repo": "github.com/org/repo",
  "branch": "main",
  "bug_link": "https://yunjie/bug/123",
  "bug_desc": "下单接口偶发 500，报错 NullPointerException"
}

Response: 202 Accepted
{
  "task_id": "rca-2026-0831-0001"
}
```

```http
GET /api/v1/rca/{task_id}/stream
Accept: text/event-stream

Response: text/event-stream
event: stage_start
data: {"task_id":"rca-2026-0831-0001","stage":"A1","ts":1735000000}

event: stage_complete
data: {"task_id":"rca-2026-0831-0001","stage":"A1","summary":{"suspect_services":["OrderService","PaymentClient"]}}

event: gate_pending
data: {"task_id":"rca-2026-0831-0001","gate":"HIL","reason":"low_confidence","top_confidence":0.42}

event: gate_resolved
data: {"task_id":"rca-2026-0831-0001","gate":"HIL","action":"confirmed"}

event: final
data: {"task_id":"rca-2026-0831-0001","top3":[...],"solution":{...}}

event: error
data: {"task_id":"rca-2026-0831-0001","code":"A2_GRAPH_DEGRADED","message":"CodeGraph build timeout"}
```

```python
def run(state: RCAState) -> RCAState:
    graph = build_state_machine()
    return graph.invoke(state, config={"checkpoint": "redis"})
```

### 数据结构

```python
class RCAState(BaseModel):
    bug_info: BugInfo
    symptoms: list[str]
    error_type: str
    query: str
    suspect_services: list[str]
    S_static: list[SuspectFunction]
    P_runtime: AnomalyPath
    top3: list[RootCause]
    gate_status: GateStatus
    solution: Solution
    stage: Stage

class SuspectFunction(BaseModel):
    func_id: str
    func_name: str
    call_path: list[str]
    static_depth: float

class AnomalyPath(BaseModel):
    span_tree: dict
    propagation_path: list[str]
    functions: list[str]
    runtime_anomaly: float

class RootCause(BaseModel):
    root_cause: str
    confidence: float
    evidence_chain: list[str]
    located_function: str

class GateStatus(BaseModel):
    crag: Literal["relevant", "ambiguous", "irrelevant", "passed"]
    hil: Literal["pending", "confirmed", "modified", "rejected", "skipped"]
```

Agent I/O 契约：

```python
def a1_understand(bug_info: BugInfo) -> A1Output:
    ...

class A1Output(BaseModel):
    symptoms: list[str]
    error_type: str
    query: str
    suspect_services: list[str]

def a2_code_analysis(suspect_services: list[str], error_stack: list[str]) -> list[SuspectFunction]:
    ...

def a3_trace_analysis(time_window: TimeWindow, suspect_services: list[str]) -> AnomalyPath:
    ...

def a4_root_cause(S_static: list[SuspectFunction], P_runtime: AnomalyPath) -> list[RootCause]:
    ...

def a5_solution(top3: list[RootCause]) -> Solution:
    ...
```

### 配置项

```python
LANGGRAPH_CONFIG = {
    "topology": "START->A1->(A2||A3)->A4->CRAG->HIL->A5->END",
    "parallel_branches": ["A2", "A3"],
    "fan_in_node": "A4",
    "checkpoint_backend": "redis",
}

CELERY_CONFIG = {
    "broker_url": "redis://localhost:6379/0",
    "result_backend": "redis://localhost:6379/1",
    "task_default_queue": "rca_pipeline",
    "task_time_limit": 180,
    "task_soft_time_limit": 150,
}

REDIS_CONFIG = {
    "state_key_prefix": "rca:state:",
    "event_stream_key": "rca:events:",
    "ttl_seconds": 86400,
}

SCORE_WEIGHTS = {"w1": 0.35, "w2": 0.30, "w3": 0.20, "w4": 0.15}
HIL_CONFIDENCE_THRESHOLD = 0.6
TOP_K = 3
```

## 验收指标
- Top-1 根因命中率 ≥ 80%
- Top-3 覆盖率 ≥ 95%
- P95 端到端响应 ≤ 12s
- 可用性 ≥ 99.5%
- M1：端到端 < 30s
- M2：Top-3 命中率 ≥ 75%
- 误报率（双闸门后）≤ 5%

## UT 测试方案

> 测试框架：Python pytest + pytest-asyncio；LangGraph 状态机用 mock Agent；Celery 用 eager 模式（CELERY_TASK_ALWAYS_EAGER=True）；Redis 用 fakeredis；SSE 用 httpx AsyncClient。

### 用例 1: test_rcastate_schema
- **被测组件**: `models/rca_state.py`（RCAState Pydantic 模型）
- **输入**: 合法 11 字段 dict；缺字段 dict；非法 Stage 枚举值
- **预期输出**: 合法输入校验通过且 11 字段齐全；缺字段抛 `ValidationError`；非法 Stage 抛枚举错误；`model_dump_json()`→`model_validate_json()` 往返一致
- **mock 策略**: 无外部依赖，纯 Pydantic 校验，不引入 mock

### 用例 2: test_langgraph_topology
- **被测组件**: `orchestrator/state_machine.py`（`build_state_machine`）
- **输入**: 状态机构建调用（无业务输入）
- **预期输出**: 拓扑边集合 = {START→A1, A1→A2, A1→A3, A2→A4, A3→A4, A4→CRAG, CRAG→HIL, HIL→A5, A5→END}；`parallel_branches=[A2,A3]`；`fan_in_node=A4`
- **mock 策略**: 5 个 Agent 均替换为 no-op mock callable，仅断言图节点与边结构

### 用例 3: test_fanout_fanin
- **被测组件**: 状态机 fan-out/fan-in 调度层（条件边 + join）
- **输入**: A1 产出后的中间状态
- **预期输出**: A2/A3 启动时间差 ≤ 50ms 互不阻塞；A4 仅在 A2 与 A3 均完成后触发；提前到达的分支结果暂存不丢失
- **mock 策略**: mock A2/A3 为带 `asyncio.sleep` 的 callable 制造耗时差异；mock A4 记录被调用时刻戳

### 用例 4: test_a1_bug_understanding
- **被测组件**: `agents/a1_understand.py`
- **输入**: Bug 单链接 + `BugInfo`
- **预期输出**: `symptoms`/`error_type`/`query`/`suspect_services` 四字段非空且类型正确
- **mock 策略**: mock 云捷 Adapter 返回固定 Bug 单 JSON；mock LLM 抽取返回四字段结构化输出

### 用例 5: test_a1_fetch_fallback
- **被测组件**: `agents/a1_understand.py`（Bug 单拉取失败回退分支）
- **输入**: 无效 `bug_link` + 非空 `bug_desc`；二者皆空样本
- **预期输出**: 回退从 `bug_desc` 抽取四字段；二者皆空时返回 `A1_BUG_FETCH_ERROR` 且 task failed
- **mock 策略**: mock 云捷 Adapter 抛 404/超时；mock LLM 从纯文本抽取

### 用例 6: test_a2_code_analysis
- **被测组件**: `agents/a2_code_analysis.py`
- **输入**: `suspect_services` + `error_stack`
- **预期输出**: `S_static` 含 `func_id`/`func_name`/`call_path`/`static_depth`；`call_path` 深度 > 0
- **mock 策略**: mock CodeGraph 返回固定调用图节点与边；mock opencode 拉码返回仓库路径

### 用例 7: test_a2_graph_degraded
- **被测组件**: `agents/a2_code_analysis.py`（CodeGraph 构建失败降级分支）
- **输入**: 触发 CodeGraph 构建超时的样本
- **预期输出**: 降级 ripgrep 输出仅含 `func_name` 的 `S_static`（`call_path` 为空）；标注 `A2_GRAPH_DEGRADED`
- **mock 策略**: mock CodeGraph 构建抛 `TimeoutError`；mock ripgrep 子进程返回匹配行

### 用例 8: test_a3_trace_analysis
- **被测组件**: `agents/a3_trace_analysis.py`
- **输入**: `time_window` + `suspect_services`
- **预期输出**: `P_runtime` 含 `span_tree`/`propagation_path`/`functions`/`runtime_anomaly`；异常 span 映射到函数
- **mock 策略**: mock Trace Adapter 返回固定 span 树 JSON

### 用例 9: test_a3_trace_missing
- **被测组件**: `agents/a3_trace_analysis.py`（Trace 不可用降级分支）
- **输入**: Trace Adapter 不可用或空时间窗
- **预期输出**: `P_runtime` 置空；标注 `A3_TRACE_MISSING`；A4 三维兜底可继续
- **mock 策略**: mock Trace Adapter 抛 `ConnectionError` 或返回空 spans 列表

### 用例 10: test_a4_rootcause_score
- **被测组件**: `agents/a4_rootcause.py`（score 计算）
- **输入**: 固定 `S_static` + `P_runtime`（已知 `static_depth`/`runtime_anomaly`/`metric_corr`/`change_recency`）
- **预期输出**: `score = 0.35*static_depth + 0.30*runtime_anomaly + 0.20*metric_corr + 0.15*change_recency`，与手算值一致（容差 1e-6）
- **mock 策略**: 注入固定维度数据，无外部 mock

### 用例 11: test_a4_topk_pruning
- **被测组件**: `agents/a4_rootcause.py`（剪枝）
- **输入**: 5 个候选函数及其 score
- **预期输出**: 保留 score 最高的 Top-3（`TOP_K=3`），按降序排列
- **mock 策略**: 注入候选列表，无 mock

### 用例 12: test_a4_low_confidence_hil
- **被测组件**: `agents/a4_rootcause.py`（gate_status 触发）
- **输入**: `top_confidence` = 0.59 / 0.60 / 0.61 三组边界样本
- **预期输出**: 0.59→`gate_status=HIL_PENDING`；0.60→放行；0.61→放行（< τ=0.6 触发）
- **mock 策略**: 注入固定置信度候选，无 mock

### 用例 13: test_a4_no_candidate
- **被测组件**: `agents/a4_rootcause.py`（空候选分支）
- **输入**: `S_static` ∩ `P_runtime.functions` 交集为空
- **预期输出**: `top3=[]` + 标注 `A4_NO_CANDIDATE` + 强制 `gate_status=HIL_PENDING`
- **mock 策略**: 注入无交集数据

### 用例 14: test_celery_async_dispatch
- **被测组件**: `tasks/rca_pipeline.py`
- **输入**: 提交一个 RCA 任务
- **预期输出**: eager 模式下同步执行完成；任务投递到 `task_default_queue=rca_pipeline`；返回 `task_id`
- **mock 策略**: `CELERY_TASK_ALWAYS_EAGER=True` + `CELERY_TASK_EAGER_PROPAGATES=True`；fakeredis 作 broker/result_backend

### 用例 15: test_redis_state_persist
- **被测组件**: 状态持久化层（RedisAdapter）
- **输入**: 一个 `RCAState` 实例 + `task_id`
- **预期输出**: 写入 `rca:state:{task_id}`，值为 RCAState JSON；`TTL=86400s`；可正确反序列化还原 11 字段
- **mock 策略**: fakeredis `FakeRedis` 客户端

### 用例 16: test_breakpoint_resume
- **被测组件**: 状态机断点续跑恢复逻辑
- **输入**: `stage=A4` 的已持久化 RCAState（A1/A2/A3 已完成）
- **预期输出**: 恢复后跳过 A1/A2/A3，从 A4 续跑，不重跑已完成阶段
- **mock 策略**: fakeredis 预置 `stage=A4` 状态；mock A4/A5 为 spy 记录调用次数

### 用例 17: test_sse_stream
- **被测组件**: `api/rca_router.py`（SSE 端点）
- **输入**: 订阅某 `task_id` 的 stream
- **预期输出**: 收到 `stage_start`/`stage_complete` 进度事件 + `final` 结果事件，事件序列有序
- **mock 策略**: mock 流水线向 fakeredis 事件流写入阶段事件；httpx `AsyncClient` 消费 SSE

### 用例 18: test_degraded_mode
- **被测组件**: 降级模式逻辑
- **输入**: LLM/Trace/CodeGraph 配额耗尽信号
- **预期输出**: `degraded_dimensions` 含对应维度；仍以可用维度产出 Top-3；报告含降级标记
- **mock 策略**: mock 上游返回 429 限流；mock 可用维度 Agent 正常返回

### 用例 19: test_uncaught_exception
- **被测组件**: 全局异常捕获兜底
- **输入**: 注入 A4 抛出未捕获异常
- **预期输出**: `stage=FAILED`；SSE 推送 `error` 事件；task 状态 failed；错误堆栈写入 Redis
- **mock 策略**: mock A4 抛 `RuntimeError`；fakeredis 记录 state 与事件流

### 用例 20: test_a5_solution_generation
- **被测组件**: `agents/a5_solution.py`
- **输入**: Top-3 `RootCause`
- **预期输出**: `Solution` 含 `patch_suggestion`/`test_cases`/`historical_cases`；内容基于 Top-3
- **mock 策略**: mock LightRAG high-level 检索返回历史案例

## E2E 测试方案

> 测试框架同 UT；E2E 统一使用 Celery eager（CELERY_TASK_ALWAYS_EAGER=True）+ fakeredis + httpx AsyncClient，不依赖真实云捷/Trace/CodeGraph/LLM 上游。

### 场景 1: e2e_full_rca_pipeline
- **前置条件**: 云捷 Bug 单可用（mock）、CodeGraph 与 Trace 正常（mock）、Celery eager + fakeredis 就绪
- **测试步骤**: `POST /api/v1/rca/analyze` 提交 Bug单 → 获取 `task_id` → 等待流水线完成 → 拉取最终 `RCAState`
- **预期结果**: 依次经过 A1→A2∥A3→A4→CRAG→HIL→A5，产出 Top-3 + Solution
- **断言点**: `RCAState` 11 字段流转正确；`stage` 终态为 `A5_COMPLETED`；`top3` 长度=3；`solution.patch_suggestion` 非空

### 场景 2: e2e_sse_progress_stream
- **前置条件**: 同场景 1
- **测试步骤**: 提交分析 → httpx `AsyncClient` 订阅 `GET /api/v1/rca/{task_id}/stream` → 收集全部 SSE 事件
- **预期结果**: 每个 stage 收到 `stage_start` + `stage_complete`；最终收到 `final` 事件
- **断言点**: 事件序列有序且完整；`stage_complete` 携带产物摘要；`final` 含 `top3`+`solution`

### 场景 3: e2e_hil_human_loop
- **前置条件**: 注入低置信样本（`top_confidence`<0.6）
- **测试步骤**: 提交 → 收到 `gate_pending` 事件 → 模拟人工 POST 确认/修正 → A5 重新生成
- **预期结果**: `gate_status` 从 `HIL_PENDING`→`confirmed`；A5 基于人工输入重生成方案
- **断言点**: `gate_pending` 事件触发；人工回灌后 `gate_resolved` 事件；最终 `solution` 反映人工输入

### 场景 4: e2e_breakpoint_resume
- **前置条件**: 流水线运行到 A4 时模拟进程崩溃；`RCAState` 已持久化到 Redis
- **测试步骤**: 崩溃 → 重启 worker → 从 fakeredis 恢复 → 续跑 A4→A5
- **预期结果**: A1/A2/A3 不重跑；从 A4 恢复继续至完成
- **断言点**: 恢复后 `stage` 推进正确；A1/A2/A3 的 `stage_complete` 事件不重复触发

### 场景 5: e2e_degraded_pipeline
- **前置条件**: CodeGraph 构建不可用（mock 超时）
- **测试步骤**: 提交 → A2 降级 ripgrep → A4 兜底 → 产出 Top-3
- **预期结果**: 标注 `A2_GRAPH_DEGRADED`；仍产出 Top-3
- **断言点**: `degraded_dimensions` 含 `codegraph`；`top3` 非空；报告含降级标记

### 场景 6: e2e_no_trace_pipeline
- **前置条件**: Trace Adapter 不可用（mock）
- **测试步骤**: 提交 → A3 降级 → A4 仅静态+指标+变更三维 → 产出 Top-3
- **预期结果**: 标注 `A3_TRACE_MISSING`；score 降权（`runtime_anomaly` 维度=0）；仍产出 Top-3
- **断言点**: `P_runtime` 置空；`top3` 非空；score 计算跳过 runtime 维度

### 场景 7: e2e_rest_api_analyze
- **前置条件**: API 服务启动 + Celery eager + fakeredis
- **测试步骤**: `POST /api/v1/rca/analyze` → 断言 202 + `task_id` → `GET stream` 订阅 → 收集 `final`
- **预期结果**: 接口返回 202 与 `task_id`；SSE 最终推送 `final` 含完整结果
- **断言点**: `task_id` 格式 `rca-YYYYMMDD-NNNN`；HTTP 状态 202；`final` 事件 `top3`+`solution` 完整

### 场景 8: e2e_concurrent_analysis
- **前置条件**: 多个 Bug单样本就绪
- **测试步骤**: 并发提交 N 个分析任务 → 各自订阅 SSE → 校验状态隔离
- **预期结果**: 各任务互不串扰；Redis `state_key` 各自独立
- **断言点**: 每个 `task_id` 的 `rca:state:{task_id}` 不冲突；SSE 事件按 `task_id` 路由正确；结果不串号

## 跨模块集成测试方案

> 集成测试聚焦"模块边界契约"，验证 5-Agent 编排层与上下游模块（Bug单 Adapter / CodeGraph / 代码中文描述 / Trace / CMDB / LightRAG / 双图谱 / LLM / 双闸门 / 知识飞轮 / 前端 SSE）的真实数据交互与数据契约一致性。集成测试统一使用 fakeredis + Celery eager + mock 全部上游，不产生真实外部调用。

### 上下游依赖关系表

| Agent | 上游模块 | 上游数据契约 | 下游模块 | 下游数据契约 |
|-------|---------|------------|---------|------------|
| A1 问题理解 | Bug单 Adapter（云捷拉单）、opencode LLM（抽取） | Bug单 JSON：bug_id/title/description/error_stack/components | 状态机 A2∥A3 fan-out、前端 SSE stage 事件 | A1Output：symptoms/error_type/query/suspect_services |
| A2 代码分析 | CodeGraph（MCP callers/callees）、代码中文描述（MCP code2cn_outline）、opencode LLM | callers/callees 节点与边、函数中文大纲文本 | 状态机 A4 fan-in | A2Output S_static：func_id/func_name/call_path/static_depth |
| A3 链路分析 | Trace Adapter、CMDB（服务拓扑）、opencode LLM | span 树 JSON、服务调用关系 | 状态机 A4 fan-in | A3Output P_runtime：span_tree/propagation_path/functions/runtime_anomaly |
| A4 根因分析 | LightRAG（aquery 检索）、双图谱交叉验证（cross_validate Top-3）、opencode LLM | 检索结果列表、Candidate 列表 | 双闸门 CRAG + HIL | A4Output Top-3 RootCause：root_cause/confidence/evidence_chain/located_function |
| A5 方案生成 | LightRAG（high-level 检索）、opencode LLM | 历史修复 + 最佳实践检索 | 知识飞轮（回写）、前端 SSE final 事件 | A5Output Solution：fix_summary/patch_snippet/verification_steps |

### 集成测试场景

#### 场景 integ_a1_bug_adapter — A1 ← Bug单 Adapter（拉单 + 鉴权 + 四字段抽取）
- **涉及模块**: A1 问题理解 Agent、Bug单 Adapter（云捷）、opencode LLM
- **集成点**: Bug单 Adapter 拉单接口 + 鉴权 + LLM 四字段抽取
- **测试步骤**: ①注入 bug_link + 鉴权 token → ②mock Bug单 Adapter 返回预设 Bug单 JSON → ③mock LLM 返回四字段 → ④调用 a1_understand → ⑤断言 A1Output 字段
- **预期结果**: Bug单成功拉取并鉴权通过；四字段（symptoms/error_type/query/suspect_services）从 Bug单 JSON 正确抽取
- **断言点**: Adapter 被调用且携带 token；error_stack 解析为 error_type=NullPointerException；suspect_services 含 OrderService；A1Output 写入 RCAState

#### 场景 integ_a2_codegraph_code2cn — A2 ← CodeGraph + 代码中文描述 → S_static
- **涉及模块**: A2 代码分析 Agent、CodeGraph（MCP callers/callees）、代码中文描述（MCP code2cn_outline）、opencode LLM
- **集成点**: CodeGraph MCP 调用图查询 + code2cn_outline 大纲查询 → S_static 输出
- **测试步骤**: ①注入 suspect_services + error_stack → ②mock CodeGraph 返回 callers/callees 节点边 → ③mock code2cn_outline 返回函数中文大纲 → ④A2 反向 BFS → ⑤断言 S_static
- **预期结果**: 静态污点追踪沿调用图反向 BFS 产出 S_static，每项含调用路径与中文大纲
- **断言点**: callers/callees 均被查询；call_path 深度 > 0；func_name 与 code2cn_outline 大纲一致；static_depth 正确

#### 场景 integ_a3_trace_cmdb — A3 ← Trace Adapter + CMDB → P_runtime
- **涉及模块**: A3 链路分析 Agent、Trace Adapter、CMDB（服务拓扑）、opencode LLM
- **集成点**: Trace Adapter span 拉取 + CMDB 服务拓扑 → P_runtime 输出
- **测试步骤**: ①注入 time_window + suspect_services → ②mock Trace Adapter 返回预设 span 树 → ③mock CMDB 返回服务拓扑 → ④A3 重建 span 树 + 异常识别 → ⑤断言 P_runtime
- **预期结果**: span 树重建正确，异常 span 映射到函数，输出 P_runtime
- **断言点**: span_tree 结构正确；propagation_path 非空；functions 与 CMDB 服务映射一致；runtime_anomaly ∈ [0,1]

#### 场景 integ_a4_lightrag_dualgraph — A4 ← LightRAG + 双图谱 → Top-3 RootCause
- **涉及模块**: A4 根因分析 Agent、LightRAG（aquery）、双图谱交叉验证（cross_validate）、opencode LLM
- **集成点**: LightRAG aquery 检索 + 双图谱 cross_validate → Top-3 RootCause 输出
- **测试步骤**: ①注入 S_static + P_runtime → ②mock LightRAG aquery 返回检索结果 → ③mock 双图谱 cross_validate 返回 Candidate 列表 → ④A4 四维关联 + score 排序 → ⑤断言 Top-3
- **预期结果**: 四维交集函数置信度 boost，按 score 降序输出 Top-3
- **断言点**: score = 0.35*static_depth + 0.30*runtime_anomaly + 0.20*metric_corr + 0.15*change_recency；交集函数 confidence 提升；Top-3 长度=3；每条四字段完整

#### 场景 integ_a4_to_gate — A4 Top-3 → 双闸门 CRAG + HIL（confidence<τ 触发）
- **涉及模块**: A4 根因分析 Agent、双闸门（CRAG + HIL，对接 build-dual-gate-flywheel）、前端 SSE
- **集成点**: A4 Top-3 输出 → CRAG 评估 → confidence<τ 触发 HIL
- **测试步骤**: ①注入低置信 Top-3（top_confidence=0.42）→ ②A4 输出 → ③CRAG 评估相关性 → ④断言 HIL 触发 → ⑤断言 gate_status 与 SSE
- **预期结果**: confidence<τ=0.6 触发 HIL，gate_status=HIL_PENDING，SSE 推送 gate_pending 事件
- **断言点**: top_confidence=0.42 < 0.6；gate_status.crag 评估档位正确；gate_status.hil=pending；gate_pending 事件携带 top_confidence

#### 场景 integ_a5_to_flywheel_frontend — A5 方案 → 知识飞轮回写 + 前端 SSE 推送
- **涉及模块**: A5 方案生成 Agent、知识飞轮（对接 build-dual-gate-flywheel 回写）、前端 SSE
- **集成点**: A5 Solution 输出 → 知识飞轮回写 + SSE final 推送
- **测试步骤**: ①注入通过闸门的 Top-3 → ②mock LightRAG 返回历史案例 → ③A5 生成 Solution → ④断言飞轮回写 → ⑤断言 SSE final 事件
- **预期结果**: Solution 生成并回写知识飞轮；SSE 推送 final 事件含 top3 + solution
- **断言点**: 飞轮回写接口被调用且携带 Solution；SSE final 事件含 top3+solution；httpx 收到 final 事件

#### 场景 integ_full_pipeline_5agent — Bug单 → A1→A2∥A3→A4→CRAG→HIL→A5 → 方案 + 飞轮回写全链路
- **涉及模块**: 5-Agent 编排全链路、Bug单 Adapter、CodeGraph、Trace、LightRAG、双图谱、双闸门、知识飞轮、前端 SSE
- **集成点**: 端到端 `POST /api/v1/rca/analyze` → 状态机全拓扑 → 方案 + 飞轮回写
- **测试步骤**: ①POST 提交 Bug单 → ②获取 task_id → ③mock 全部上游 → ④等待流水线完成 → ⑤断言全链路 RCAState + 飞轮回写 + SSE
- **预期结果**: 依次经过 A1→A2∥A3→A4→CRAG→HIL→A5，产出 Top-3 + Solution，飞轮回写，SSE final 推送
- **断言点**: RCAState 11 字段流转正确；stage 终态=A5_COMPLETED；top3 长度=3；飞轮回写被调用；SSE final 含 top3+solution；无真实外部调用

## 测试数据与 Mock 规范

### 1. 测试数据构造策略

#### RCAState Fixture 工厂
- 提供 `make_rca_state(stage=...)` 工厂函数，按 stage 预填对应阶段产物（A1 阶段填充 bug_info/symptoms/error_type/query/suspect_services；A2 阶段追加 S_static；A3 阶段追加 P_runtime；A4 阶段追加 top3/gate_status；A5 阶段追加 solution）
- 工厂内置边界样本：低置信（top_confidence=0.42）、空候选（top3=[]）、降级（degraded_dimensions）等

#### 各 Agent I/O Fixture
- 每个 Agent 提供输入 Fixture（A1: bug_link+bug_desc；A2: suspect_services+error_stack；A3: time_window+suspect_services；A4: S_static+P_runtime；A5: top3）与输出 Fixture（A1Output/A2Output/A3Output/A4Output/A5Output）
- Fixture 与 spec 数据契约一一对应，字段名与类型严格一致

#### conftest.py 全局 fixture
- `celery_eager`：设置 `CELERY_TASK_ALWAYS_EAGER=True` + `CELERY_TASK_EAGER_PROPAGATES=True`
- `fakeredis_client`：`fakeredis.FakeRedis()` 实例，注入 RedisAdapter
- `mock_bug_adapter` / `mock_codegraph` / `mock_trace` / `mock_lightrag` / `mock_dualgraph` / `mock_llm`：注册全部上游 mock
- `fastapi_client`：FastAPI TestClient + httpx AsyncClient 共享 fakeredis 上下文

#### fakeredis
- 全程使用 `fakeredis.FakeRedis`，不启动真实 Redis；broker/result_backend 指向 fakeredis；state_key 与 event_stream_key 均落在 fakeredis

#### Celery eager
- `CELERY_TASK_ALWAYS_EAGER=True`：任务同步执行不实际入队；`CELERY_TASK_EAGER_PROPAGATES=True`：异常直接抛出便于断言

### 2. Mock 数据样本

#### RCAState 完整样本 JSON（11 字段）
```json
{
  "bug_info": {
    "bug_id": "BUG-2026-0831-0001",
    "bug_link": "https://yunjie/bug/BUG-2026-0831-0001",
    "title": "下单接口偶发 500 NullPointerException",
    "description": "下单接口偶发返回 500，日志报 NullPointerException",
    "repo": "github.com/org/order-service",
    "branch": "main",
    "error_stack": [
      "java.lang.NullPointerException",
      "at com.order.OrderService.createOrder(OrderService.java:128)",
      "at com.order.PaymentClient.charge(PaymentClient.java:64)"
    ],
    "components": ["OrderService", "PaymentClient"]
  },
  "symptoms": ["下单接口偶发 500", "NullPointerException", "订单创建失败"],
  "error_type": "NullPointerException",
  "query": "order service NullPointerException createOrder charge",
  "suspect_services": ["OrderService", "PaymentClient"],
  "S_static": [
    {
      "func_id": "fn_001",
      "func_name": "com.order.OrderService.createOrder",
      "call_path": ["com.order.OrderController.place", "com.order.OrderService.createOrder"],
      "static_depth": 0.85
    },
    {
      "func_id": "fn_002",
      "func_name": "com.order.PaymentClient.charge",
      "call_path": ["com.order.OrderService.createOrder", "com.order.PaymentClient.charge"],
      "static_depth": 0.72
    }
  ],
  "P_runtime": {
    "span_tree": {
      "span_id": "span_root",
      "service": "OrderService",
      "operation": "POST /api/order",
      "children": [
        {"span_id": "span_1", "service": "OrderService", "operation": "createOrder", "anomaly": true, "duration_ms": 520}
      ]
    },
    "propagation_path": ["OrderService.createOrder", "PaymentClient.charge"],
    "functions": ["com.order.OrderService.createOrder", "com.order.PaymentClient.charge"],
    "runtime_anomaly": 0.78
  },
  "top3": [
    {
      "root_cause": "createOrder 未校验入参为空导致 NPE",
      "confidence": 0.82,
      "evidence_chain": ["静态调用路径命中", "运行时 span 异常", "变更近 2h"],
      "located_function": "com.order.OrderService.createOrder"
    },
    {
      "root_cause": "PaymentClient.charge 空指针解引用",
      "confidence": 0.61,
      "evidence_chain": ["运行时 span 异常"],
      "located_function": "com.order.PaymentClient.charge"
    },
    {
      "root_cause": "insufficient_evidence",
      "confidence": 0,
      "evidence_chain": [],
      "located_function": ""
    }
  ],
  "gate_status": {"crag": "passed", "hil": "skipped"},
  "solution": {
    "fix_summary": "在 createOrder 入口增加空值校验并返回 400",
    "patch_snippet": "if (order == null || order.getUserId() == null) { throw new IllegalArgumentException(\"order invalid\"); }",
    "verification_steps": ["单测覆盖空入参", "回归下单主链路", "灰度上线观察错误率"],
    "historical_cases": [{"case_id": "case_0102", "summary": "历史 NPE 修复范式"}]
  },
  "stage": "A5_COMPLETED"
}
```

#### Bug单样本 JSON
```json
{
  "bug_id": "BUG-2026-0831-0001",
  "title": "下单接口偶发 500 NullPointerException",
  "description": "下单接口偶发返回 500，日志报 NullPointerException，影响订单创建主链路",
  "error_stack": [
    "java.lang.NullPointerException",
    "at com.order.OrderService.createOrder(OrderService.java:128)",
    "at com.order.PaymentClient.charge(PaymentClient.java:64)"
  ],
  "components": ["OrderService", "PaymentClient"]
}
```

#### A1Output 样本
```json
{
  "symptoms": ["下单接口偶发 500", "NullPointerException", "订单创建失败"],
  "error_type": "NullPointerException",
  "query": "order service NullPointerException createOrder charge",
  "suspect_services": ["OrderService", "PaymentClient"]
}
```

#### A2Output S_static 样本
```json
{
  "S_static": [
    {
      "func_id": "fn_001",
      "func_name": "com.order.OrderService.createOrder",
      "call_path": ["com.order.OrderController.place", "com.order.OrderService.createOrder"],
      "static_depth": 0.85
    },
    {
      "func_id": "fn_002",
      "func_name": "com.order.PaymentClient.charge",
      "call_path": ["com.order.OrderService.createOrder", "com.order.PaymentClient.charge"],
      "static_depth": 0.72
    }
  ]
}
```

#### A3Output P_runtime 样本
```json
{
  "P_runtime": {
    "span_tree": {
      "span_id": "span_root",
      "service": "OrderService",
      "operation": "POST /api/order",
      "children": [
        {"span_id": "span_1", "service": "OrderService", "operation": "createOrder", "anomaly": true, "duration_ms": 520}
      ]
    },
    "propagation_path": ["OrderService.createOrder", "PaymentClient.charge"],
    "functions": ["com.order.OrderService.createOrder", "com.order.PaymentClient.charge"],
    "runtime_anomaly": 0.78
  }
}
```

#### A4Output Top-3 RootCause 样本
```json
{
  "top3": [
    {
      "root_cause": "createOrder 未校验入参为空导致 NPE",
      "confidence": 0.82,
      "evidence_chain": ["静态调用路径命中", "运行时 span 异常", "变更近 2h"],
      "located_function": "com.order.OrderService.createOrder"
    },
    {
      "root_cause": "PaymentClient.charge 空指针解引用",
      "confidence": 0.61,
      "evidence_chain": ["运行时 span 异常"],
      "located_function": "com.order.PaymentClient.charge"
    },
    {
      "root_cause": "insufficient_evidence",
      "confidence": 0,
      "evidence_chain": [],
      "located_function": ""
    }
  ]
}
```

#### A5Output Solution 样本
```json
{
  "solution": {
    "fix_summary": "在 createOrder 入口增加空值校验并返回 400",
    "patch_snippet": "if (order == null || order.getUserId() == null) { throw new IllegalArgumentException(\"order invalid\"); }",
    "verification_steps": ["单测覆盖空入参", "回归下单主链路", "灰度上线观察错误率"],
    "historical_cases": [{"case_id": "case_0102", "summary": "历史 NPE 修复范式"}]
  }
}
```

#### SSE 事件流样本（stage 进度事件 + final 结果事件）
```json
[
  {"event": "stage_start", "data": {"task_id": "rca-2026-0831-0001", "stage": "A1", "ts": 1735000000}},
  {"event": "stage_complete", "data": {"task_id": "rca-2026-0831-0001", "stage": "A1", "summary": {"suspect_services": ["OrderService", "PaymentClient"]}}},
  {"event": "stage_start", "data": {"task_id": "rca-2026-0831-0001", "stage": "A4", "ts": 1735000012}},
  {"event": "stage_complete", "data": {"task_id": "rca-2026-0831-0001", "stage": "A4", "summary": {"top_confidence": 0.82}}},
  {"event": "final", "data": {"task_id": "rca-2026-0831-0001", "top3": [{"root_cause": "createOrder 未校验入参为空导致 NPE", "confidence": 0.82}], "solution": {"fix_summary": "在 createOrder 入口增加空值校验"}}}
]
```

#### LLM 响应 Mock（A1 抽取 / A2 分析 / A4 推理 / A5 生成 各一）
```json
// A1 抽取
{"symptoms": ["下单接口偶发 500", "NullPointerException"], "error_type": "NullPointerException", "query": "order service NullPointerException createOrder", "suspect_services": ["OrderService", "PaymentClient"]}

// A2 分析
{"analysis": "createOrder 为异常入口，沿调用图反向 BFS 命中 PaymentClient.charge", "candidates": [{"func_id": "fn_001", "func_name": "com.order.OrderService.createOrder", "static_depth": 0.85}]}

// A4 推理
{"root_cause": "createOrder 未校验入参为空导致 NPE", "confidence": 0.82, "evidence_chain": ["静态调用路径命中", "运行时 span 异常"], "located_function": "com.order.OrderService.createOrder"}

// A5 生成
{"fix_summary": "在 createOrder 入口增加空值校验并返回 400", "patch_snippet": "if (order == null) { throw new IllegalArgumentException(); }", "verification_steps": ["单测覆盖空入参"]}
```

### 3. Mock 规范

#### Bug单 Adapter mock
- 接口：`yunjie.fetch_bug(bug_link, token) -> Bug单JSON`
- mock 返回预设 Bug单样本 JSON（见上文）；鉴权失败返回 401；链接无效返回 404；超时抛 `TimeoutError` 触发 bug_desc 回退

#### CodeGraph MCP mock
- 接口：`codegraph.callers(func_id)` / `codegraph.callees(func_id)` / `codegraph.code2cn_outline(func_id)`
- mock 返回预设调用图节点边与中文大纲；构建超时抛 `TimeoutError` 触发 ripgrep 降级标注 `A2_GRAPH_DEGRADED`

#### Trace Adapter mock
- 接口：`trace.fetch_spans(time_window, service) -> spanTreeJSON`
- mock 返回预设 span 树；不可用抛 `ConnectionError`；空时间窗返回空 spans 列表触发 `A3_TRACE_MISSING`

#### LightRAG mock
- 接口：`lightrag.aquery(query, mode="high-level") -> 检索结果`
- mock 返回预设检索结果列表（含历史案例）；为空时返回 `[]` 触发 `A5_NO_HISTORY`

#### 双图谱 cross_validate mock
- 接口：`dualgraph.cross_validate(S_static, P_runtime) -> list[Candidate]`
- mock 返回预设 Candidate 列表（含交集函数与置信度 boost）；交集为空返回 `[]` 触发 `A4_NO_CANDIDATE`

#### LLM mock
- 按 Agent 角色返回预设响应：A1 抽取返回四字段；A2 分析返回候选函数；A4 推理返回 root_cause+confidence；A5 生成返回补丁+用例
- 限流时返回 429 触发降级模式（degraded_dimensions 标注 llm）

#### Celery
- `CELERY_TASK_ALWAYS_EAGER=True`：同步执行不实际入队 broker；`CELERY_TASK_EAGER_PROPAGATES=True`：异常直接抛出便于断言

#### Redis
- 全程 `fakeredis.FakeRedis`，不启动真实 Redis；broker/result_backend/state/event_stream 均落在 fakeredis

#### SSE
- 使用 httpx `AsyncClient` 真实订阅 FastAPI TestClient 暴露的 SSE 端点，收集事件序列断言有序完整

### 4. 测试数据库初始化

- fakeredis 作为统一测试数据库：`redis_client = fakeredis.FakeRedis()`
- RCAState JSON 序列化：`redis_client.set(f"rca:state:{task_id}", state.model_dump_json())`
- state_key_prefix=`rca:state:`，event_stream_key=`rca:events:{task_id}`，TTL=86400s（fakeredis 支持 `expire`）
- 测试前置：`conftest.py` 中 fixture 初始化 fakeredis 并清空（`fakeredis.flushall()`），保证用例隔离
- 断点续跑测试：向 fakeredis 预置 `stage=A4` 的 RCAState JSON，断言恢复逻辑跳过已完成阶段

### 5. Fixture 文件组织

```
tests/fixtures/engine/
├── rca_state/
│   ├── full_state.json          # RCAState 完整 11 字段样本
│   ├── low_confidence.json      # 低置信样本（top_confidence=0.42）
│   ├── no_candidate.json        # 空候选样本
│   └── degraded.json            # 降级样本
├── agent_io/
│   ├── bug_ticket.json          # Bug单样本
│   ├── a1_output.json           # A1Output 样本
│   ├── a2_output_s_static.json  # A2Output S_static 样本
│   ├── a3_output_p_runtime.json # A3Output P_runtime 样本
│   ├── a4_output_top3.json      # A4Output Top-3 RootCause 样本
│   └── a5_output_solution.json  # A5Output Solution 样本
├── sse/
│   ├── stage_events.json        # SSE 阶段进度事件样本
│   └── final_event.json         # SSE final 结果事件样本
└── llm/
    ├── a1_extract.json          # LLM A1 抽取响应
    ├── a2_analyze.json          # LLM A2 分析响应
    ├── a4_reason.json           # LLM A4 推理响应
    └── a5_generate.json         # LLM A5 生成响应
```

约定：Fixture 文件以 JSON 存储于 `tests/fixtures/engine/` 下，按 `rca_state` / `agent_io` / `sse` / `llm` 分类组织；用例通过 `pytest` fixture 加载，确保与 spec 数据契约字段名与类型严格一致。

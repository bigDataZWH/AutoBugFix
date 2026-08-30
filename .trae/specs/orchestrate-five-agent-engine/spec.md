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

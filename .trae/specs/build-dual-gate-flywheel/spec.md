# 双闸门与知识飞轮 Spec

## Why
LLM 根因推理存在幻觉风险，且系统准确率不会随使用自提升。需要"双闸门"强制收敛（CRAG 机器层自动纠偏 + HIL 人工确认门）压低误报，并以"知识飞轮"将工单解决产物结构化回写知识库，使平台随工单积累自学习、越用越准。结构闸门校验调用路径完整性，语义闸门校验根因可解释性，双闸门齐开方放行。

## What Changes
- **新增 CRAG 自动纠偏门（机器层）**：在 A4 根因分析前嵌入 `crag_gate` 接口，由 LightRAG 重排器评估证据相关性，三档处理——相关→精炼、模糊→补检、不相关→改写 Query 重检索。`BREAKING`：RCA 流水线在 A1→A4 之间新增强制闸门节点，原有直连检索→A4 的调用链路被切断。
- **新增 HIL 人工确认门（人）**：A4 输出 Top-3 根因后嵌入 `hil_gate` 接口，Top-3 置信度低于阈值 τ 时挂起任务，经前端 SSE 推送确认面板，人工确认/修正/驳回后回灌 A4。`BREAKING`：A4→A5 由同步直连改为异步可挂起状态机，引入 `task_status=HANG` 挂起态与人工回灌事件。
- **新增双闸门串联**：检索证据 → CRAG 门（机器，LightRAG 重排器评估）→ A4 根因 → HIL 门（人，置信度 < τ 挂起）→ A5 方案；仅双闸门齐开方放行。`BREAKING`：A5 方案生成的触发条件从"A4 完成"改为"双闸门齐开"。
- **新增结构闸门与语义闸门校验**：结构闸门校验 Top-3 根因的调用路径完整性（call_path 非空且可达），语义闸门校验根因可解释性（root_cause 文本可追溯至证据链）。
- **新增知识飞轮回写**：工单解决后经 `flywheel_writeback` 抽取根因/根因函数/调用路径/修复补丁/验证用例 → BGE-M3 向量化 → LightRAG `ainsert` 入库 → 图谱 `SIMILAR_TO` 边 → 增量更新社区摘要。
- **新增自学习闭环**：向量与图谱持续增量更新，系统准确率随使用量单调提升，知识积累从静态样例库升级为飞轮式增量自演化。

## Impact
- **Affected specs**: setup-lightrag-retrieval-engine（CRAG 重排器、飞轮 `ainsert`、SIMILAR_TO 边、社区摘要增量依赖）、orchestrate-five-agent-engine（闸门位于 A4→A5 之间、HIL 回灌 A4、A4→A5 触发条件变更）、implement-dual-graph-validation（双闸门依赖交叉验证结论作为 CRAG/HIL 输入）、deploy-win11-local（前端确认面板 SSE 推送通道）
- **Affected code**: `gate/crag_gate.py`（CRAG 闸门模块）、`gate/hil_gate.py`（HIL 闸门与状态机）、`gate/structure_semantic_gate.py`（结构/语义闸门校验）、`frontend/SSEPanel.vue`（前端确认面板）、`flywheel/writeback_extractor.py`（飞轮回写抽取器）、`flywheel/similar_edge_builder.py`（SIMILAR_TO 边构建）、`flywheel/community_summary_updater.py`（社区摘要增量更新）、`langgraph/nodes.py`（RCAState 新增 gate_state 字段）

## ADDED Requirements

### Requirement: CRAG 自动纠偏门
系统 SHALL 在检索证据进入 A4 根因分析前设置 CRAG 机器层闸门，由 LightRAG 重排器评估证据相关性，输出三档判定 `verdict ∈ {relevant, ambiguous, irrelevant}`，并据此执行精炼/补检/改写 Query 重检索。

#### Scenario: CRAG 相关证据精炼
- **WHEN** `crag_gate` 收到证据列表且重排器评估 `verdict="relevant"`
- **THEN** 返回 `refined_evidence`（去噪后的精炼证据），不附带 `rewritten_query`，直接放行至 A4 根因分析

#### Scenario: CRAG 模糊证据补检
- **WHEN** 重排器评估 `verdict="ambiguous"`（证据部分相关但置信不足）
- **THEN** 返回 `augmented_query` 触发补充检索，将补检证据并入 `refined_evidence` 后重新评估，直至收敛为 `relevant` 或达到最大补检轮次

#### Scenario: CRAG 不相关证据改写重检索
- **WHEN** 重排器评估 `verdict="irrelevant"`（证据与症状/报错无关）
- **THEN** 生成 `rewritten_query`（基于症状/报错类型改写检索 Query），重新检索并再次过 `crag_gate`，形成 CRAG 内部循环

#### Scenario: CRAG 改写重检索达上限仍不相关（边缘场景）
- **WHEN** 改写 Query 重检索达到 `max_rewrite_rounds`（默认 3）仍为 `irrelevant`
- **THEN** 标记证据为"低置信不可用"，降级触发 HIL 门强制人工介入，并在报告中记录 CRAG 纠偏失败链路

#### Scenario: CRAG 门证据为空（错误场景）
- **WHEN** `crag_gate` 收到空证据列表 `evidence=[]`
- **THEN** 直接返回 `verdict="irrelevant"` 且 `rewritten_query` 取自 A1 症状/报错原始 Query，跳过精炼，记录空证据告警

### Requirement: HIL 人工确认门
系统 SHALL 在 A4 输出 Top-3 根因后设置 HIL 人工确认门，当 Top-3 置信度低于阈值 τ 时挂起任务（`task_status=HANG`），经前端 SSE 推送确认面板，人工确认/修正/驳回后回灌 A4。

#### Scenario: HIL 低置信挂起
- **WHEN** `hil_gate` 收到 Top-3 根因且 `confidence < τ`
- **THEN** 返回 `action="hang"` 与 `panel_payload`，任务状态置为 `HANG`，经 SSE 推送确认面板至前端，等待人工决策

#### Scenario: HIL 高置信放行（阈值 τ 边界）
- **WHEN** Top-3 根因 `confidence ≥ τ`（含恰好等于 τ 的边界情况）
- **THEN** 返回 `action="pass"`，跳过人工确认，直接进入双闸门串联放行判定

#### Scenario: HIL 人工确认
- **WHEN** 人工在确认面板选择"确认"，`HIL decision.action="confirm"`
- **THEN** Top-3 根因原样回灌 A4→A5，任务从 `HANG` 恢复为 `RUNNING`，放行至 A5 方案生成

#### Scenario: HIL 修正回灌
- **WHEN** 人工选择"修正"，`HIL decision.action="modify"` 且携带 `modified_top3`
- **THEN** 以 `modified_top3` 替换原 Top-3 回灌 A4，A5 基于修正后根因生成方案，记录修正 diff 用于飞轮学习

#### Scenario: HIL 驳回（错误场景）
- **WHEN** 人工选择"驳回"，`HIL decision.action="reject"` 且携带 `feedback`
- **THEN** 任务状态置为 `REJECTED`，终止 A5 生成，将驳回原因与原始证据回灌 A1 重新触发症状/报错抽取

### Requirement: 双闸门串联放行
系统 SHALL 串联 CRAG 门与 HIL 门，结构闸门校验调用路径完整性、语义闸门校验根因可解释性，仅当双闸门齐开（机器层证据达标 + 人工层确认 + 结构/语义校验通过）方放行至 A5 方案生成。

#### Scenario: 双闸门齐开放行
- **WHEN** CRAG 门 `verdict="relevant"` 且 HIL 门 `action="pass"` 或人工确认通过，且结构闸门调用路径完整、语义闸门根因可解释
- **THEN** 放行至 A5 方案生成，A5 基于 LightRAG high-level 检索历史修复 + 最佳实践产出补丁与验证用例

#### Scenario: 结构闸门校验失败（错误场景）
- **WHEN** Top-3 根因的 `call_path` 为空或不可达（结构闸门校验调用路径完整性未通过）
- **THEN** 阻断放行，回退至 A2/A3 重新构建代码图谱与链路，记录结构闸门失败事件

#### Scenario: 语义闸门校验失败（错误场景）
- **WHEN** `root_cause` 文本无法追溯至证据链（语义闸门校验根因可解释性未通过）
- **THEN** 阻断放行，回退至 A4 重算根因并要求补充证据链标注，记录语义闸门失败事件

#### Scenario: 单门通过未放行
- **WHEN** CRAG 门通过但 HIL 门挂起，或 HIL 门通过但 CRAG 门判定为 `irrelevant`
- **THEN** 不放行至 A5，等待未通过门完成收敛后再判定

### Requirement: 知识飞轮回写
系统 SHALL 在工单解决后结构化回写知识库：经 `flywheel_writeback` 抽取根因/根因函数/调用路径/修复补丁/验证用例 → BGE-M3 向量化 → LightRAG `ainsert` 入库 → 图谱 `SIMILAR_TO` 边 → 增量更新社区摘要。

#### Scenario: 飞轮回写
- **WHEN** 工单状态转为"已解决"
- **THEN** `flywheel_writeback` 抽取结构化产物 payload，BGE-M3 向量化后 `ainsert` 入库，建立 `SIMILAR_TO` 边，增量更新社区摘要，返回 `{inserted, similar_edges}`

#### Scenario: 飞轮回写去重（边缘场景）
- **WHEN** 回写 payload 与已有知识库案例相似度超过去重阈值（向量余弦相似度 ≥ 0.95）
- **THEN** 不重复 `ainsert`，仅更新已有节点的 `SIMILAR_TO` 边权重，返回 `inserted=false`

#### Scenario: 飞轮回写失败重试（错误场景）
- **WHEN** `ainsert` 或 SIMILAR_TO 边写入失败
- **THEN** 进入异步重试队列，重试 3 次仍失败则告警并落盘待回写 payload，不阻塞工单关闭

### Requirement: 自学习闭环
系统 SHALL 通过知识飞轮使向量与图谱持续增量更新，系统准确率随使用量单调提升，知识积累从静态样例库升级为飞轮式增量自演化。

#### Scenario: 越用越准
- **WHEN** 工单持续积累并经飞轮回写入库
- **THEN** Top-3 命中率随案例增多单调提升，新案例可被 `SIMILAR_TO` 检索命中，形成"越用越准"闭环

#### Scenario: 新案例 SIMILAR_TO 命中
- **WHEN** 新工单进入 RCA 流程，飞轮库中已积累相似历史案例
- **THEN** A5 检索阶段经 `SIMILAR_TO` 边命中相似历史修复方案，提升方案复用率与准确率

## 技术细节

### 接口定义

```python
def crag_gate(evidence: List[Evidence]) -> CragTriage:
    """
    CRAG 自动纠偏门：LightRAG 重排器评估证据相关性，三档处理。
    :param evidence: A1-A3 检索得到的证据列表
    :return: CragTriage，含 verdict / refined_evidence / augmented_query / rewritten_query
    """

def hil_gate(top3: List[RootCause], confidence: float) -> HilResult:
    """
    HIL 人工确认门：Top-3 置信度 < τ 时挂起任务并推送确认面板。
    :param top3: A4 输出的 Top-3 根因
    :param confidence: Top-3 综合置信度
    :return: HilResult，action ∈ {pass, hang}，hang 时附带 panel_payload
    """

def flywheel_writeback(resolved_ticket: ResolvedTicket) -> WritebackResult:
    """
    知识飞轮回写：工单解决后抽取结构化产物并入库。
    :param resolved_ticket: 已解决工单（含根因/补丁/验证用例）
    :return: WritebackResult，含 inserted 计数与 similar_edges 列表
    """

# 前端 SSE 推送确认面板（事件流）
# event: hil_panel
# data: {"task_id": "...", "panel_payload": {...}, "top3": [...]}
```

### 数据结构

```python
class CragTriage:
    verdict: Literal["relevant", "ambiguous", "irrelevant"]
    refined_evidence: List[Evidence]
    augmented_query: Optional[str]
    rewritten_query: Optional[str]

class HilDecision:
    task_id: str
    action: Literal["confirm", "modify", "reject"]
    modified_top3: Optional[List[RootCause]]
    feedback: Optional[str]

class FlywheelPayload:
    root_cause: str
    root_cause_function: str
    call_path: List[str]
    fix_patch: str
    verify_case: str

class SimilarToEdge:
    src_id: str
    tgt_id: str
    type: Literal["SIMILAR_TO"]
    weight: float

class HilResult:
    action: Literal["pass", "hang"]
    panel_payload: Optional[PanelPayload]

class WritebackResult:
    inserted: int
    similar_edges: List[SimilarToEdge]
```

### 配置项

```yaml
gate:
  confidence_threshold_tau: 0.7
  max_rewrite_rounds: 3
  max_supplement_rounds: 2
  dedup_cosine_threshold: 0.95
  structure_gate:
    check_call_path_complete: true
  semantic_gate:
    check_root_cause_explainable: true
  hil:
    sse_channel: "hil_panel"
    hang_status: "HANG"
    retry_queue_max: 3
flywheel:
  embedding_model: "BGE-M3"
  lightrag_ainsert: true
  similar_edge_type: "SIMILAR_TO"
  community_summary_incremental: true
```

## 验收指标
- 双闸门后误报率 ≤ 5%（A5 方案对实际根因的误报比例）
- 误报率较无双闸门基线下降 ≥ 40%
- 知识飞轮使 Top-3 准确率随案例数单调提升（相邻 50 案例窗口准确率不下降）
- 新案例可被 `SIMILAR_TO` 边命中，命中率随库规模增长
- CRAG 门三档处理正确率：相关精炼/模糊补检/不相关改写分类准确率 ≥ 90%
- HIL 门挂起触发准确率：`confidence < τ` 判定与人工实际需介入一致率 ≥ 95%
- 飞轮回写延迟：工单解决到 `ainsert` 完成 ≤ 30s
- 结构闸门调用路径完整性校验覆盖率 100%，语义闸门根因可解释性校验覆盖率 100%

## UT 测试方案

> 测试框架：Python `pytest` + `pytest-asyncio`。LightRAG 重排器与 `ainsert` 用 mock；BGE-M3 向量化用 mock embedding（返回固定 dim=1024 向量）；HIL 确认面板用 mock 回调注入人工决策。测试配置 `gate.confidence_threshold_tau=0.6`、`gate.max_rewrite_rounds=3`、`gate.dedup_cosine_threshold=0.95`。目标覆盖率 ≥ 85%（line + branch）。每个用例含：用例名、被测组件、输入、预期输出、mock 策略。

### UT-1: test_crag_triage_relevant
- **被测组件**：`gate/crag_gate.py` → `crag_gate(evidence)`
- **输入**：3 条与症状强相关证据（高相关度）
- **预期输出**：`CragTriage.verdict="relevant"`，`refined_evidence` 非空（去噪后），`augmented_query=None`，`rewritten_query=None`，直通放行至 A4
- **mock 策略**：mock LightRAG 重排器返回高 relevance score，映射 `relevant`

### UT-2: test_crag_triage_ambiguous
- **被测组件**：`gate/crag_gate.py` → `crag_gate(evidence)`
- **输入**：部分相关但置信不足的证据
- **预期输出**：`verdict="ambiguous"`，`augmented_query` 非空（触发补充检索），补检证据并入 `refined_evidence`
- **mock 策略**：mock 重排器返回中等 score → ambiguous；mock 补充检索返回额外证据

### UT-3: test_crag_triage_irrelevant
- **被测组件**：`gate/crag_gate.py` → `crag_gate(evidence)`
- **输入**：与症状/报错无关的证据
- **预期输出**：`verdict="irrelevant"`，`rewritten_query` 非空（基于症状改写 Query），触发重检索
- **mock 策略**：mock 重排器返回低 score → irrelevant；mock Query 改写器生成 `rewritten_query`

### UT-4: test_crag_rewrite_rounds
- **被测组件**：`gate/crag_gate.py` → `crag_gate(evidence)`（内部改写循环）
- **输入**：持续 `irrelevant` 的证据，`max_rewrite_rounds=3`
- **预期输出**：改写至第 3 轮仍 `irrelevant` 时停止重写，降级标记"低置信不可用"并触发 HIL 强制人工介入，记录失败链路
- **mock 策略**：mock 重排器连续返回 irrelevant；断言改写轮次计数 ≤ 3，降级路径触发一次

### UT-5: test_hil_threshold_trigger
- **被测组件**：`gate/hil_gate.py` → `hil_gate(top3, confidence)`
- **输入**：Top-3 根因，`confidence=0.55 < τ=0.6`
- **预期输出**：`HilResult.action="hang"`，`gate_status=HIL_PENDING`（任务置 `task_status=HANG`），`panel_payload` 非空
- **mock 策略**：mock SSE 推送回调断言 `panel_payload` 已推送；不调用真实前端

### UT-6: test_hil_threshold_pass
- **被测组件**：`gate/hil_gate.py` → `hil_gate(top3, confidence)`
- **输入**：Top-3 根因，`confidence=0.6 == τ`（边界）
- **预期输出**：`HilResult.action="pass"`，无挂起，不推送面板，直通双闸门串联放行
- **mock 策略**：mock SSE 推送回调断言未被调用

### UT-7: test_hil_decision_accept
- **被测组件**：`gate/hil_gate.py`（人工决策回灌分支）
- **输入**：`HilDecision(action="confirm")`
- **预期输出**：Top-3 原样回灌 A4→A5，`task_status` HANG→RUNNING，放行至 A5
- **mock 策略**：mock 人工决策回调注入 confirm；断言状态机迁移正确

### UT-8: test_hil_decision_reject
- **被测组件**：`gate/hil_gate.py`（驳回分支）
- **输入**：`HilDecision(action="reject", feedback="根因不符")`
- **预期输出**：`task_status=REJECTED`，终止 A5，回退 A4 重分析（回灌证据与 feedback）
- **mock 策略**：mock 人工决策回调注入 reject；断言 A5 未触发、A4 重算触发

### UT-9: test_hil_timeout
- **被测组件**：`gate/hil_gate.py`（超时降级）
- **输入**：挂起后人工未响应，达到超时时限
- **预期输出**：降级自动通过（`action="pass"`），并标记 `timeout_degraded=true`，记录超时事件
- **mock 策略**：mock 定时器/事件循环推进超时；断言降级标记与日志

### UT-10: test_flywheel_payload_schema
- **被测组件**：`flywheel/writeback_extractor.py` → `FlywheelPayload`
- **输入**：已解决工单的原始解决方案文本
- **预期输出**：`FlywheelPayload` 含 root_cause/root_cause_function/call_path/fix_patch/verify_case 五字段且类型合规
- **mock 策略**：纯 schema 校验，无外部依赖；缺字段时断言抛 ValidationError

### UT-11: test_flywheel_extract
- **被测组件**：`flywheel/writeback_extractor.py` → 抽取器
- **输入**：已解决工单（含根因/补丁/验证用例）
- **预期输出**：正确抽取 root_cause/root_cause_function/call_path/fix_patch/verify_case 五要素
- **mock 策略**：mock LLM 抽取返回结构化产物；断言字段映射正确

### UT-12: test_flywheel_vectorize
- **被测组件**：BGE-M3 向量化封装
- **输入**：root_cause 文本
- **预期输出**：dim=1024 的 float 向量
- **mock 策略**：mock BGE-M3 模型返回固定 1024 维向量；断言维度与归一化

### UT-13: test_flywheel_ainsert
- **被测组件**：`flywheel/writeback_extractor.py` → `flywheel_writeback()`（ainsert 段）
- **输入**：向量化后的 FlywheelPayload
- **预期输出**：LightRAG `ainsert` 被调用，实体写入，`WritebackResult.inserted=1`
- **mock 策略**：mock LightRAG.ainsert 记录调用参数；断言实体内容写入

### UT-14: test_similar_to_edge
- **被测组件**：`flywheel/similar_edge_builder.py`
- **输入**：新根因向量 + 历史根因向量集
- **预期输出**：`SimilarToEdge(type="SIMILAR_TO")` 边创建，weight=余弦相似度
- **mock 策略**：mock 相似度计算返回固定值；mock 图谱边写入；断言边 type 与 weight

### UT-15: test_community_summary_update
- **被测组件**：`flywheel/community_summary_updater.py`
- **输入**：新实体插入事件
- **预期输出**：社区摘要重算并增量更新
- **mock 策略**：mock LightRAG 社区摘要接口；断言 update 被调用且摘要含新实体

### UT-16: test_crag_hil_sequence
- **被测组件**：双闸门串联（CRAG→HIL）
- **输入**：证据 + Top-3 根因
- **预期输出**：CRAG 先过滤（收敛 relevant 后）才进入 HIL 判定；CRAG=irrelevant 时不触发 HIL
- **mock 策略**：mock crag_gate 与 hil_gate，断言调用顺序与短路逻辑

### UT-17: test_gate_bypass
- **被测组件**：双闸门串联放行判定
- **输入**：`confidence≥τ` 且 CRAG=relevant 且结构/语义闸门通过
- **预期输出**：直通 A5，不触发任何闸门挂起，无面板推送
- **mock 策略**：mock 各闸门返回通过态；断言无 HIL 回调、A5 触发一次

## E2E 测试方案

> E2E 覆盖双闸门与飞轮全链路，使用真实 RCAState 状态机编排，LightRAG/BGE-M3/SSE 仍以 mock 替换外部依赖（隔离真实模型与前端）。每个场景含：场景名、前置条件、测试步骤、预期结果、断言点。

### E2E-1: e2e_crag_hil_full_gate
- **前置条件**：RCA 流水线 A1-A3 已产出证据，A4 输出 Top-3 根因，τ=0.6
- **测试步骤**：
  1. A4 输出 Top-3（`confidence=0.55 < τ`）
  2. CRAG 门对证据三分类，收敛为 `relevant`
  3. HIL 门判定 `confidence<τ` → 挂起，推送面板
  4. mock 人工决策 `confirm`
  5. 双闸门齐开 → 放行 A5
- **预期结果**：A5 方案生成成功，任务 RUNNING→HANG→RUNNING
- **断言点**：CRAG `verdict=relevant`；HIL `action=hang→confirm`；`gate_state` 双门齐开；A5 产物非空

### E2E-2: e2e_flywheel_writeback
- **前置条件**：存在已解决工单（含根因/补丁/验证用例）
- **测试步骤**：
  1. 工单状态转"已解决"触发 `flywheel_writeback`
  2. 抽取 FlywheelPayload
  3. BGE-M3 向量化
  4. LightRAG `ainsert` 入库
  5. 构建 SIMILAR_TO 边
  6. 增量更新社区摘要
- **预期结果**：`WritebackResult.inserted=1`，`similar_edges` 非空
- **断言点**：`ainsert` 调用一次；SIMILAR_TO 边创建（`type=SIMILAR_TO`）；社区摘要 update 调用；回写延迟 ≤ 30s

### E2E-3: e2e_hil_human_loop
- **前置条件**：低置信 Top-3（`confidence<τ`）
- **测试步骤**：
  1. HIL 挂起推送面板
  2. mock 人工输入 `modify`（修正 Top-3）
  3. 回退 A4 重新生成
  4. 新 Top-3 `confidence≥τ` → 放行
- **预期结果**：修正后根因回灌 A4，A5 基于修正根因生成
- **断言点**：`modified_top3` 替换原 Top-3；修正 diff 记录；A5 基于修正根因

### E2E-4: e2e_crag_rewrite_loop
- **前置条件**：证据 ambiguous/irrelevant，`max_rewrite_rounds=3`
- **测试步骤**：
  1. CRAG 判 ambiguous → 补检
  2. 仍 ambiguous/irrelevant → 改写 Query 重检索
  3. 循环至收敛 `relevant` 或达上限
- **预期结果**：收敛 `relevant` 放行，或达上限降级 HIL
- **断言点**：改写轮次 ≤ 3；收敛路径 `verdict=relevant` 或降级触发 HIL

### E2E-5: e2e_flywheel_reuse
- **前置条件**：飞轮库已积累相似历史根因
- **测试步骤**：
  1. 新工单进入 RCA
  2. A5 检索经 SIMILAR_TO 边命中历史修复方案
  3. 命中后 confidence 提升
- **预期结果**：新工单命中历史根因，置信度提升
- **断言点**：SIMILAR_TO 边被遍历命中；命中率 > 0；confidence 较未命中基线提升

### E2E-6: e2e_high_confidence_bypass
- **前置条件**：Top-3 `confidence≥τ`
- **测试步骤**：
  1. A4 输出高置信 Top-3
  2. CRAG=relevant
  3. HIL 判定 `confidence≥τ` → pass
  4. 跳过 HIL 直接 A5
- **预期结果**：无面板推送，直通 A5
- **断言点**：HIL `action="pass"`；SSE 面板回调未触发；A5 触发一次

## 跨模块集成测试方案

> 集成测试聚焦跨模块边界契约，验证双闸门（CRAG/HIL）与知识飞轮在真实上下游链路中的数据流转与状态迁移。与 UT（单组件）和 E2E（全链路状态机编排）的区别：集成测试以「模块间接口契约 + 数据结构流转」为粒度，mock 上游 Agent（A4/A5）与外部模型（LightRAG reranker/ainsert、BGE-M3），但 `crag_gate`/`hil_gate`/`flywheel_writeback` 业务逻辑真实执行。测试配置 `gate.confidence_threshold_tau=0.6`、`gate.max_rewrite_rounds=3`、`gate.dedup_cosine_threshold=0.95`。

### 上下游依赖关系表

| 方向 | 模块 | 上游输入 | 下游输出 | 关键数据结构 | 闸门/阈值 |
| --- | --- | --- | --- | --- | --- |
| 上游 | 5-Agent A4 | A1-A3 检索证据 + CRAG 精炼证据 | Top-3 RootCause candidates + confidence | RootCause, confidence | — |
| 上游 | LightRAG（CRAG 重排序检索） | A1-A3 原始证据 | relevant/ambiguous/irrelevant 三分类 | CragTriage | max_rewrite_rounds=3 |
| 上游 | 人工 HIL 面板 | HIL 挂起的 panel_payload | accept/reject/timeout 决策 | HilDecision | τ=0.6 |
| 上游 | 5-Agent A5 | 双闸门齐开放行的 Top-3 根因 | Solution 方案（飞轮提取来源） | Solution | — |
| 下游 | 5-Agent A5 | 闸门决策后继续方案生成 | fix_summary/patch_snippet/verification_steps | Solution | — |
| 下游 | LightRAG（飞轮回写） | FlywheelPayload 向量化结果 | ainsert 实体 + SIMILAR_TO 边 | FlywheelPayload, SimilarToEdge | dedup_cosine_threshold=0.95 |
| 下游 | 社区摘要 | ainsert 新实体事件 | 增量更新社区摘要 | CommunitySummary | — |

### 集成测试场景

#### integ_agent4_to_crag — A4 Top-3 → CRAG 重排序三分类
- **涉及模块**：5-Agent A4、`gate/crag_gate.py`、LightRAG reranker
- **集成点**：A4 检索证据输出 → `crag_gate(evidence)` 入参；LightRAG 重排器三分类返回
- **测试步骤**：
  1. mock A4 Agent 输出 Top-3 candidates（含高/低 confidence 两类样本）
  2. 调用 `crag_gate(evidence)` 真实执行
  3. mock LightRAG reranker 返回三档 relevance score
  4. 断言 `CragTriage.verdict` 与 reranker score 映射一致
- **预期结果**：高 score→`relevant`（精炼放行），中 score→`ambiguous`（补检），低 score→`irrelevant`（改写重检索）
- **断言点**：`verdict ∈ {relevant, ambiguous, irrelevant}`；`relevant` 时 `rewritten_query=None`；`irrelevant` 时 `rewritten_query` 非空；reranker 被调用且参数为原始 evidence

#### integ_crag_to_hil — CRAG ambiguous/低置信 → HIL 阈值触发
- **涉及模块**：`gate/crag_gate.py`、`gate/hil_gate.py`、HIL 面板
- **集成点**：CRAG 收敛后 `confidence` 传递至 `hil_gate(top3, confidence)`；`confidence<τ=0.6` 触发挂起
- **测试步骤**：
  1. CRAG 判定 `ambiguous` 经补检收敛为 `relevant`，但 A4 输出 `confidence=0.55`
  2. 调用 `hil_gate(top3, confidence=0.55)` 真实执行
  3. 断言阈值触发逻辑
- **预期结果**：`confidence=0.55 < τ=0.6` → `HilResult.action="hang"`，`gate_state=HIL_PENDING`，`task_status=HANG`
- **断言点**：`action="hang"`；`panel_payload` 非空；SSE mock 回调被调用一次；CRAG 与 HIL 调用顺序正确（CRAG 先收敛再进 HIL）

#### integ_hil_to_agent5 — HIL 人工决策 accept → A5 继续 / reject → 回退 A4
- **涉及模块**：`gate/hil_gate.py`、5-Agent A5、5-Agent A4
- **集成点**：`HilDecision` 回调 → A5 触发或 A4 回退；状态机 `HANG`→`RUNNING`/`REJECTED`
- **测试步骤**：
  1. HIL 挂起后注入 mock 人工决策 `HilDecision(action="confirm")` → 断言 A5 触发
  2. 注入 `HilDecision(action="reject", feedback="根因不符")` → 断言 A5 未触发、A4 回退重算
  3. 注入超时决策 → 断言降级 `pass` + `timeout_degraded=true`
- **预期结果**：accept 路径 `HANG→RUNNING` 放行 A5；reject 路径 `task_status=REJECTED` 终止 A5 回退 A4；timeout 降级放行
- **断言点**：accept 时 A5 mock 被调用一次且入参为原 Top-3；reject 时 A5 mock 未调用、A4 mock 被调用（回退）；timeout 时 `timeout_degraded=true`

#### integ_agent5_to_flywheel — A5 Solution → 飞轮提取
- **涉及模块**：5-Agent A5、`flywheel/writeback_extractor.py`
- **集成点**：A5 Solution 输出 → `flywheel_writeback` 抽取 `FlywheelPayload`
- **测试步骤**：
  1. mock A5 返回 Solution（fix_summary/patch_snippet/verification_steps）
  2. 工单转"已解决"触发 `flywheel_writeback` 真实执行
  3. 抽取器从 Solution 抽取结构化产物
- **预期结果**：`FlywheelPayload` 含 root_cause/root_cause_function/call_path/fix_patch/verify_case 五字段完整
- **断言点**：五字段非空且类型合规；`root_cause_function` 映射自 Solution 函数定位；`fix_patch` 映射自 `patch_snippet`；`verify_case` 映射自 `verification_steps`

#### integ_flywheel_to_lightrag_ainsert — 飞轮向量化 → LightRAG ainsert → SIMILAR_TO 边 → 社区摘要
- **涉及模块**：`flywheel/writeback_extractor.py`、`flywheel/similar_edge_builder.py`、`flywheel/community_summary_updater.py`、LightRAG ainsert、BGE-M3
- **集成点**：FlywheelPayload → BGE-M3 向量化 → LightRAG `ainsert` → `SIMILAR_TO` 边创建 → 社区摘要增量更新
- **测试步骤**：
  1. 构造 `FlywheelPayload` 样本
  2. mock BGE-M3 返回 dim=1024 向量
  3. 调用 `flywheel_writeback` 真实执行（ainsert/边/摘要 mock）
  4. 断言 ainsert 调用参数与 SIMILAR_TO 边创建
- **预期结果**：`WritebackResult.inserted=1`，`similar_edges` 非空，社区摘要 update 被调用
- **断言点**：BGE-M3 mock 被调用且输出 dim=1024；LightRAG `ainsert` 被调用一次且入参含向量化 payload；`SimilarToEdge.type="SIMILAR_TO"`、`weight=余弦相似度`；社区摘要 update 被调用

#### integ_full_pipeline_gate_flywheel — A4 → CRAG → HIL → A5 → 飞轮回写 → 后续工单复用全链路
- **涉及模块**：5-Agent A4、CRAG 门、HIL 门、5-Agent A5、飞轮回写、LightRAG、后续工单检索
- **集成点**：全链路状态机 `gate_state` 贯穿；飞轮回写后 SIMILAR_TO 边可被后续工单 A5 检索命中
- **测试步骤**：
  1. mock A4 输出低置信 Top-3（`confidence=0.55`）
  2. CRAG 收敛 `relevant` → HIL 挂起 → mock 人工 `confirm` → 放行 A5
  3. A5 产出 Solution → 飞轮回写（ainsert + SIMILAR_TO 边）
  4. 构造后续相似工单进入 RCA，A5 检索经 SIMILAR_TO 边命中历史方案
- **预期结果**：全链路状态机迁移正确（RUNNING→HANG→RUNNING→已解决）；后续工单命中历史根因，confidence 提升
- **断言点**：`gate_state` 双门齐开记录；飞轮 `inserted=1`；后续工单 SIMILAR_TO 边被遍历命中；后续 confidence > 首单 confidence

## 测试数据与 Mock 规范

### 测试数据构造策略

- **Fixture 工厂模式**：为 `CragTriage`/`HilDecision`/`FlywheelPayload`/`SimilarToEdge`/`Solution` 各建一个 Fixture 工厂函数，支持参数化覆盖（如 `make_crag_triage(verdict="relevant")`、`make_hil_decision(action="accept")`），避免散落硬编码 JSON。
- **conftest.py 集中注册**：在 `tests/conftest.py` 注册所有 Fixture 工厂与 Mock 注册器（`mock_lightrag_reranker`、`mock_lightrag_ainsert`、`mock_bge_m3`、`mock_hil_panel`、`mock_agent_a4`、`mock_agent_a5`），统一注入测试配置（τ=0.6、max_rewrite_rounds=3）。
- **参数化覆盖**：使用 `@pytest.mark.parametrize` 对三档 verdict、三路径决策、阈值边界（0.59/0.60/0.61）做矩阵覆盖。
- **JSON Fixture 加载**：复杂样本以 JSON 文件落盘 `tests/fixtures/gate/`，由 `conftest.py` 的 `load_fixture(path)` 加载，工厂函数在其基础上派生变体。

### Mock 数据样本

> 所有样本落盘于 `tests/fixtures/gate/` 对应子目录，字段命名与上文「数据结构」定义对齐。`CragTriage.verdict` 即分类 label，附 `score`（reranker 相关性得分）；`HilDecision.action` 即 decision，附 `resolved_confidence`（人工确认后回灌置信度）；`FlywheelPayload` 的 root_cause_function/call_path/fix_patch/verify_case 即 function/path/patch/case；`SimilarToEdge.weight` 即 similarity。

**A4 Top-3 candidates 输入样本**（含 confidence 高/低两种，`tests/fixtures/gate/crag/a4_top3_high.json` / `a4_top3_low.json`）：

```json
{
  "task_id": "tk-001",
  "top3": [
    {"rank": 1, "root_cause": "Redis 连接池耗尽导致 OOM", "call_path": ["svc.py:L42", "pool.py:L88"], "confidence": 0.82},
    {"rank": 2, "root_cause": "GC 停顿引发超时", "call_path": ["gc.py:L12"], "confidence": 0.61},
    {"rank": 3, "root_cause": "磁盘 IO 饱和", "call_path": ["disk.py:L77"], "confidence": 0.45}
  ],
  "aggregate_confidence": 0.82
}
```

```json
{
  "task_id": "tk-002",
  "top3": [
    {"rank": 1, "root_cause": "疑似缓存击穿", "call_path": ["cache.py:L30"], "confidence": 0.55},
    {"rank": 2, "root_cause": "疑似连接泄漏", "call_path": [], "confidence": 0.48},
    {"rank": 3, "root_cause": "疑似线程阻塞", "call_path": ["thread.py:L9"], "confidence": 0.41}
  ],
  "aggregate_confidence": 0.55
}
```

**CragTriage 响应样本**（relevant/ambiguous/irrelevant 三种 + rewritten_query，`tests/fixtures/gate/crag/crag_triage_*.json`）：

```json
{
  "verdict": "relevant",
  "label": "relevant",
  "score": 0.91,
  "refined_evidence": [{"id": "ev-1", "content": "pool.py 连接数达上限 200/200", "source": "log"}],
  "augmented_query": null,
  "rewritten_query": null
}
```

```json
{
  "verdict": "ambiguous",
  "label": "ambiguous",
  "score": 0.52,
  "refined_evidence": [{"id": "ev-2", "content": "部分堆栈指向 pool.py", "source": "trace"}],
  "augmented_query": "Redis 连接池 max_connections 配置 + OOM 关联证据",
  "rewritten_query": null
}
```

```json
{
  "verdict": "irrelevant",
  "label": "irrelevant",
  "score": 0.18,
  "refined_evidence": [],
  "augmented_query": null,
  "rewritten_query": "Redis OOM 连接池耗尽 根因排查"
}
```

**HilDecision 样本**（accept/reject/timeout 三种 + feedback 文本，`tests/fixtures/gate/hil/hil_decision_*.json`）：

```json
{
  "task_id": "tk-002",
  "action": "confirm",
  "decision": "accept",
  "feedback": null,
  "resolved_confidence": 0.90,
  "modified_top3": null
}
```

```json
{
  "task_id": "tk-002",
  "action": "reject",
  "decision": "reject",
  "feedback": "根因不符，实际为连接泄漏非池耗尽",
  "resolved_confidence": null,
  "modified_top3": null
}
```

```json
{
  "task_id": "tk-002",
  "action": "timeout",
  "decision": "timeout",
  "feedback": null,
  "resolved_confidence": 0.55,
  "modified_top3": null,
  "timeout_degraded": true
}
```

**FlywheelPayload 样本**（root_cause/function/path/patch/case 完整字段，`tests/fixtures/gate/flywheel/payload.json`）：

```json
{
  "root_cause": "Redis 连接池耗尽导致 OOM",
  "root_cause_function": "pool.py:get_connection",
  "call_path": ["svc.py:L42", "pool.py:L88", "pool.py:get_connection"],
  "fix_patch": "diff --git a/pool.py b/pool.py\n+max_connections=400\n+eviction_on_full=True",
  "verify_case": "test_pool_not_oom_under_pressure"
}
```

**SimilarToEdge 样本**（source/target/similarity 字段，`tests/fixtures/gate/flywheel/similar_edge.json`）：

```json
{
  "src_id": "root_cause:redis_pool_exhaust_oom",
  "tgt_id": "root_cause:redis_conn_leak_oom",
  "source": "root_cause:redis_pool_exhaust_oom",
  "target": "root_cause:redis_conn_leak_oom",
  "type": "SIMILAR_TO",
  "weight": 0.87,
  "similarity": 0.87
}
```

**A5 Solution 样本**（fix_summary/patch_snippet/verification_steps，`tests/fixtures/gate/flywheel/a5_solution.json`）：

```json
{
  "task_id": "tk-001",
  "fix_summary": "扩大 Redis 连接池上限并启用满载驱逐策略，消除 OOM",
  "patch_snippet": "max_connections=400; eviction_on_full=True",
  "verification_steps": ["压测 1000 并发连接不 OOM", "驱逐策略日志校验", "连接数监控告警阈值校准"]
}
```

**BGE-M3 embedding Mock**（dim=1024 向量，`tests/fixtures/gate/flywheel/bge_m3_embedding.json`）：

```json
{
  "model": "BGE-M3",
  "dim": 1024,
  "vector": [0.0123, -0.0456, 0.0789, 0.0, "...(共1024维)...", 0.0331],
  "normalized": true
}
```

**LightRAG ainsert Mock**（验证调用参数，`tests/fixtures/gate/flywheel/lightrag_ainsert_call.json`）：

```json
{
  "method": "ainsert",
  "called_with": {
    "entities": [{"id": "root_cause:redis_pool_exhaust_oom", "type": "RootCause", "vector_dim": 1024}],
    "content": "Redis 连接池耗尽导致 OOM | function=pool.py:get_connection | patch=max_connections=400",
    "metadata": {"source_ticket": "tk-001", "flywheel_payload_hash": "sha256:..."}
  },
  "returns": {"inserted_ids": ["root_cause:redis_pool_exhaust_oom"]}
}
```

**τ 阈值配置样本**（confidence_threshold=0.6 + max_rewrite_rounds=3，`tests/fixtures/gate/gate_config.json`）：

```json
{
  "gate": {
    "confidence_threshold_tau": 0.6,
    "max_rewrite_rounds": 3,
    "max_supplement_rounds": 2,
    "dedup_cosine_threshold": 0.95
  }
}
```

### Mock 规范

| Mock 对象 | 替换范围 | 行为契约 | 验证点 | 不 Mock（真实执行） |
| --- | --- | --- | --- | --- |
| LightRAG reranker | 三分类检索 | 按 score 阈值返回预设 `CragTriage`（≥0.75 relevant / 0.4-0.75 ambiguous / <0.4 irrelevant） | 调用入参=原始 evidence；返回 verdict 与 score 映射 | `crag_gate` 业务逻辑（精炼/补检/改写分支） |
| LightRAG ainsert | 实体/边写入 | 记录调用参数（entities/content/metadata），返回 `inserted_ids`，不实际写库 | 调用一次；入参含向量化 payload + dim=1024 | `flywheel_writeback` 编排逻辑 |
| BGE-M3 embedding | 向量化模型 | 返回固定 dim=1024 归一化向量（按输入 hash 确定性生成，便于去重测试） | 输出维度=1024；归一化 | — |
| HIL 面板 | 人工决策回调 | 模拟 accept/reject/timeout 三路径回调，注入预设 `HilDecision` | SSE 回调被调用一次；决策注入后状态机迁移 | `hil_gate` 阈值判定与状态机 |
| A4 Agent | Top-3 根因 | 返回预设 Top-3 candidates（高/低 confidence 两类） | 输出含 confidence 字段 | — |
| A5 Agent | Solution 方案 | 返回预设 Solution（fix_summary/patch_snippet/verification_steps） | 双闸门齐开后方被调用 | — |

> **真实执行边界**：`crag_gate`/`hil_gate`/`flywheel_writeback`/`similar_edge_builder`/`community_summary_updater` 的业务逻辑不 mock，仅 mock 其外部依赖（LightRAG/BGE-M3/SSE/Agent），以验证业务编排正确性。

### 测试数据库初始化

- **优先方案**：mock LightRAG Postgres 客户端层（`lightrag.storage`），用内存 dict 模拟实体/边/社区摘要存储，零外部依赖、CI 友好。
- **次选方案**：对需验证真实 SQL/图谱拓扑的场景，使用 `testcontainers` 启动一次性 Postgres + pgvector 容器，测试结束自动销毁。
- **初始化脚本**：`tests/fixtures/gate/init_db.sql` 预置历史根因实体与 SIMILAR_TO 边（≥10 条），供飞轮复用与去重场景作为基线库。
- **隔离策略**：每个集成测试用例独立事务/独立容器实例，测试间无数据污染；并发执行安全。

### Fixture 文件组织

```
tests/fixtures/gate/
├── gate_config.json                          # τ=0.6 阈值配置
├── init_db.sql                               # 测试库初始化脚本
├── crag/
│   ├── a4_top3_high.json                     # A4 高置信 Top-3
│   ├── a4_top3_low.json                      # A4 低置信 Top-3
│   ├── crag_triage_relevant.json             # CragTriage relevant
│   ├── crag_triage_ambiguous.json            # CragTriage ambiguous
│   └── crag_triage_irrelevant.json           # CragTriage irrelevant
├── hil/
│   ├── hil_decision_accept.json              # HilDecision accept
│   ├── hil_decision_reject.json              # HilDecision reject
│   └── hil_decision_timeout.json             # HilDecision timeout
└── flywheel/
    ├── payload.json                          # FlywheelPayload 完整字段
    ├── similar_edge.json                     # SimilarToEdge
    ├── a5_solution.json                      # A5 Solution
    ├── bge_m3_embedding.json                 # BGE-M3 dim=1024 向量
    └── lightrag_ainsert_call.json            # ainsert 调用参数验证
```

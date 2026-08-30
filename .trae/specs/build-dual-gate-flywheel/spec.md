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

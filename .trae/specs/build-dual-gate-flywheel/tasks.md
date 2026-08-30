# Tasks

- [ ] Task 1: 实现 CRAG 自动纠偏门
  - [ ] SubTask 1.1: 定义 `CragTriage` 数据结构（verdict/refined_evidence/augmented_query/rewritten_query）
  - [ ] SubTask 1.2: 接入 LightRAG 重排器评估证据相关性，输出三档判定 `verdict ∈ {relevant, ambiguous, irrelevant}`（对接 setup-lightrag-retrieval-engine）
  - [ ] SubTask 1.3: 实现"相关→精炼"分支：去噪证据后返回 `refined_evidence`
  - [ ] SubTask 1.4: 实现"模糊→补检"分支：生成 `augmented_query` 触发补充检索，并入证据后重新评估，限 `max_supplement_rounds`
  - [ ] SubTask 1.5: 实现"不相关→改写 Query 重检索"分支：基于症状/报错生成 `rewritten_query`，限 `max_rewrite_rounds`，形成 CRAG 内部循环
  - [ ] SubTask 1.6: 实现边缘场景——改写达上限仍 `irrelevant` 时降级触发 HIL 强制人工介入，并记录失败链路
  - [ ] SubTask 1.7: 实现错误场景——空证据列表 `evidence=[]` 时直接返回 `irrelevant` 并告警
  - **验证**: 构造三类证据样本分别命中三档，断言 `verdict` 取值正确；构造空证据断言降级路径触发；CRAG 三档分类准确率 ≥ 90%

- [ ] Task 2: 实现 HIL 人工确认门
  - [ ] SubTask 2.1: 定义 `HilDecision` 数据结构（task_id/action/modified_top3/feedback）与 `HilResult`（action/panel_payload）
  - [ ] SubTask 2.2: 实现置信度阈值 τ 判定，`confidence < τ` 时返回 `action="hang"`，`confidence ≥ τ`（含边界）返回 `action="pass"`
  - [ ] SubTask 2.3: 引入任务状态机 `task_status`，新增 `HANG` 挂起态与 `RUNNING`/`REJECTED` 态切换逻辑
  - [ ] SubTask 2.4: 前端 SSE 推送确认面板（channel=`hil_panel`，事件 `hil_panel`，data 含 task_id/panel_payload/top3，对接 deploy-win11-local L6 交互层）
  - [ ] SubTask 2.5: 实现人工确认 `action="confirm"` 分支：Top-3 原样回灌 A4→A5，`HANG`→`RUNNING`
  - [ ] SubTask 2.6: 实现人工修正 `action="modify"` 分支：以 `modified_top3` 替换原 Top-3 回灌 A4，记录修正 diff
  - [ ] SubTask 2.7: 实现人工驳回 `action="reject"` 分支：`task_status=REJECTED`，终止 A5，回灌 A1 重抽症状
  - **验证**: 断言 `confidence` 恰等于 τ 时走 `pass` 路径；模拟三种人工决策断言状态机迁移正确；HIL 挂起触发与人工实际需介入一致率 ≥ 95%

- [ ] Task 3: 实现双闸门串联放行
  - [ ] SubTask 3.1: 在 LangGraph RCAState 新增 `gate_state` 字段，串联 CRAG 门→A4 根因→HIL 门→A5 方案
  - [ ] SubTask 3.2: 实现双闸门齐开放行判定：`verdict="relevant"` 且 `action="pass"`/人工确认通过方放行至 A5
  - [ ] SubTask 3.3: 实现结构闸门校验 Top-3 根因 `call_path` 完整性（非空且可达），失败回退 A2/A3
  - [ ] SubTask 3.4: 实现语义闸门校验 `root_cause` 可解释性（可追溯至证据链），失败回退 A4 重算
  - [ ] SubTask 3.5: 实现单门通过未放行场景：CRAG/HIL 任一未通过则阻断，等待收敛后再判定
  - **验证**: 构造"双门齐开/结构失败/语义失败/单门通过"四组用例，断言放行/回退行为正确；结构/语义闸门校验覆盖率 100%

- [ ] Task 4: 实现知识飞轮回写
  - [ ] SubTask 4.1: 定义 `FlywheelPayload`（root_cause/root_cause_function/call_path/fix_patch/verify_case）与 `SimilarToEdge`（src_id/tgt_id/type/weight）
  - [ ] SubTask 4.2: 工单状态转"已解决"时触发 `flywheel_writeback(resolved_ticket)`
  - [ ] SubTask 4.3: 实现回写抽取器：从已解决工单抽取根因/根因函数/调用路径/修复补丁/验证用例
  - [ ] SubTask 4.4: BGE-M3 向量化后调用 LightRAG `ainsert` 入库（对接 setup-lightrag-retrieval-engine）
  - [ ] SubTask 4.5: 图谱建立 `SIMILAR_TO` 边（src_id→tgt_id，type=SIMILAR_TO，weight 按相似度计算）
  - [ ] SubTask 4.6: 增量更新社区摘要（community_summary_incremental=true）
  - [ ] SubTask 4.7: 实现去重场景——余弦相似度 ≥ 0.95 时不重复 `ainsert`，仅更新边权重
  - [ ] SubTask 4.8: 实现失败重试——`ainsert`/边写入失败入异步重试队列，重试 3 次落盘待回写 payload，不阻塞工单关闭
  - [ ] SubTask 4.9: 返回 `WritebackResult`（inserted 计数 + similar_edges 列表）
  - **验证**: 构造已解决工单断言 `ainsert` 被调用、SIMILAR_TO 边已建立、社区摘要已更新；构造重复 payload 断言去重生效；回写延迟 ≤ 30s

- [ ] Task 5: 验证自学习闭环
  - [ ] SubTask 5.1: 构造增量案例集（≥200 案例分 4 批），每批回写后统计 Top-3 命中率
  - [ ] SubTask 5.2: 断言命中率随案例数单调提升（相邻 50 案例窗口准确率不下降）
  - [ ] SubTask 5.3: 断言新案例可经 `SIMILAR_TO` 边检索命中历史修复方案
  - [ ] SubTask 5.4: 断言误报率较无双闸门基线下降 ≥ 40%，双闸门后误报率 ≤ 5%
  - **验证**: 输出自学习曲线，命中率单调递增；新案例 SIMILAR_TO 命中率随库规模增长；误报率达标

# Task Dependencies
- Task 1 depends on setup-lightrag-retrieval-engine（LightRAG 重排器）
- Task 2 depends on orchestrate-five-agent-engine（A4 输出与回灌）、deploy-win11-local（SSE 推送通道）
- Task 3 depends on Task 1、Task 2，且依赖 implement-dual-graph-validation（交叉验证结论作为闸门输入）
- Task 4 depends on setup-lightrag-retrieval-engine（ainsert/SIMILAR_TO/社区摘要）
- Task 5 depends on Task 4
- 双闸门位于 A4→A5 之间，依赖 implement-dual-graph-validation 的交叉验证结论

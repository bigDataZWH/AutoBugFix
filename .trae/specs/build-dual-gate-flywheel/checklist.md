# Checklist

## CRAG 自动纠偏门
- [ ] `crag_gate(evidence: List[Evidence]) -> CragTriage` 接口已实现，对接 LightRAG 重排器
- [ ] 重排器输出三档判定 `verdict ∈ {relevant, ambiguous, irrelevant}`，分类准确率 ≥ 90%
- [ ] "相关→精炼"分支：返回去噪后 `refined_evidence`，不附带 `rewritten_query`
- [ ] "模糊→补检"分支：生成 `augmented_query` 触发补充检索，限 `max_supplement_rounds`（默认 2），并入证据后重新评估至收敛
- [ ] "不相关→改写 Query 重检索"分支：基于症状/报错生成 `rewritten_query`，限 `max_rewrite_rounds`（默认 3），形成 CRAG 内部循环
- [ ] 边缘场景：改写达上限仍 `irrelevant` 时降级触发 HIL 强制人工介入，报告中记录 CRAG 纠偏失败链路
- [ ] 错误场景：空证据 `evidence=[]` 时直接返回 `irrelevant`，`rewritten_query` 取自 A1 原始 Query，记录空证据告警
- [ ] `CragTriage` 数据结构含 verdict/refined_evidence/augmented_query/rewritten_query 字段

## HIL 人工确认门
- [ ] `hil_gate(top3, confidence) -> HilResult` 接口已实现，`action ∈ {pass, hang}`
- [ ] 置信度判定：`confidence < τ` 返回 `hang`，`confidence ≥ τ`（含边界）返回 `pass`
- [ ] 阈值 τ 可在配置项 `gate.confidence_threshold_tau` 调整，默认 0.7
- [ ] 挂起时任务状态置为 `task_status=HANG`，状态机含 RUNNING/HANG/REJECTED 切换
- [ ] 前端 SSE 推送确认面板：channel=`hil_panel`，事件 `hil_panel`，data 含 task_id/panel_payload/top3
- [ ] 人工确认 `action="confirm"`：Top-3 原样回灌 A4→A5，`HANG`→`RUNNING`
- [ ] 人工修正 `action="modify"`：以 `modified_top3` 替换原 Top-3 回灌 A4，记录修正 diff
- [ ] 人工驳回 `action="reject"`：`task_status=REJECTED`，终止 A5，回灌 A1 重抽症状
- [ ] HIL 挂起触发与人工实际需介入一致率 ≥ 95%
- [ ] `HilDecision` 数据结构含 task_id/action/modified_top3/feedback 字段

## 双闸门串联放行
- [ ] 串联链路完整：检索证据 → CRAG 门（机器）→ A4 根因 → HIL 门（人）→ A5 方案
- [ ] LangGraph RCAState 新增 `gate_state` 字段，承载两门状态
- [ ] 双闸门齐开放行：`verdict="relevant"` 且 `action="pass"`/人工确认通过方放行至 A5
- [ ] 单门通过未放行：CRAG/HIL 任一未通过则阻断，等待收敛后再判定
- [ ] 结构闸门校验 Top-3 根因 `call_path` 完整性（非空且可达），失败回退 A2/A3，校验覆盖率 100%
- [ ] 语义闸门校验 `root_cause` 可解释性（可追溯至证据链），失败回退 A4 重算，校验覆盖率 100%
- [ ] A5 方案生成触发条件从"A4 完成"改为"双闸门齐开"

## 知识飞轮回写
- [ ] `flywheel_writeback(resolved_ticket) -> WritebackResult` 接口已实现，返回 `{inserted, similar_edges}`
- [ ] 工单状态转"已解决"时触发回写
- [ ] 回写抽取器抽取根因/根因函数/调用路径/修复补丁/验证用例
- [ ] BGE-M3 向量化后调用 LightRAG `ainsert` 入库
- [ ] 图谱建立 `SIMILAR_TO` 边（src_id/tgt_id/type=SIMILAR_TO/weight）
- [ ] 增量更新社区摘要（community_summary_incremental=true）
- [ ] 去重场景：余弦相似度 ≥ 0.95 时不重复 `ainsert`，仅更新边权重，`inserted=false`
- [ ] 失败重试：`ainsert`/边写入失败入异步重试队列，重试 3 次落盘待回写 payload，不阻塞工单关闭
- [ ] `FlywheelPayload` 数据结构含 root_cause/root_cause_function/call_path/fix_patch/verify_case 字段
- [ ] 回写延迟：工单解决到 `ainsert` 完成 ≤ 30s

## 自学习闭环与验收指标
- [ ] 知识积累从静态样例库升级为飞轮式增量自演化（向量与图谱持续增量更新）
- [ ] Top-3 命中率随案例数单调提升（相邻 50 案例窗口准确率不下降）
- [ ] 新案例可被 `SIMILAR_TO` 边检索命中，命中率随库规模增长
- [ ] 双闸门后误报率 ≤ 5%
- [ ] 误报率较无双闸门基线下降 ≥ 40%
- [ ] 知识飞轮使准确率随案例积累单调提升（输出自学习曲线验证）

## 测试与质量保障
- [ ] UT 覆盖率 ≥85%（line + branch）
- [ ] 17 个 UT 用例全部通过
- [ ] 6 个 E2E 场景全部通过
- [ ] CRAG 三分类 UT 通过（relevant/ambiguous/irrelevant）
- [ ] HIL 阈值 τ=0.6 触发/通过 UT 通过
- [ ] 飞轮回写 E2E 验证 SIMILAR_TO 边创建
- [ ] 飞轮复用 E2E 验证历史根因命中
- [ ] CRAG→HIL 闸门顺序 UT 通过
- [ ] CI 流水线集成 pytest 执行

## 跨模块集成测试与 Mock 规范
- [ ] 6 个跨模块集成测试场景全部通过
- [ ] CragTriage/HilDecision/FlywheelPayload/SimilarToEdge/Solution/embedding/τ配置 7 类 Mock 样本就绪
- [ ] CRAG→HIL 闸门顺序集成测试通过
- [ ] HIL accept/reject/timeout 三路径集成测试通过
- [ ] 飞轮回写 SIMILAR_TO 边创建集成测试通过
- [ ] τ=0.6 阈值触发逻辑集成测试通过
- [ ] Fixture 文件组织符合 tests/fixtures/gate/ 约定
- [ ] mock LightRAG/BGE-M3/HIL面板 不产生真实外部调用

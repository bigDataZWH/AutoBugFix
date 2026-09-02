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

- [ ] Task 6: 编写 UT 测试套件（覆盖 17 个用例，目标覆盖率 ≥85%）
  - [ ] SubTask 6.1: 搭建 pytest + pytest-asyncio 测试骨架，配置 mock LightRAG 重排器/BGE-M3/SSE 回调 fixture（测试配置 τ=0.6、max_rewrite_rounds=3）
  - [ ] SubTask 6.2: 编写 CRAG 三分类与重写轮次 UT（UT-1~UT-4，覆盖 relevant/ambiguous/irrelevant/超限降级）
  - [ ] SubTask 6.3: 编写 HIL 阈值触发/通过/决策/超时 UT（UT-5~UT-9，τ=0.6 边界 + confirm/reject/timeout）
  - [ ] SubTask 6.4: 编写飞轮 schema/抽取/向量化/ainsert/边/社区摘要 UT（UT-10~UT-15，BGE-M3 dim=1024 + SIMILAR_TO 边）
  - [ ] SubTask 6.5: 编写双闸门顺序与绕过 UT（UT-16~UT-17，CRAG→HIL 短路与高置信直通）
  - [ ] SubTask 6.6: 接入覆盖率工具（pytest-cov），输出 line+branch 报告，达标 ≥85%
  - **验证**: 17 个用例全部通过；line+branch 覆盖率 ≥85%；CRAG 三分类/HIL 阈值/闸门顺序 UT 通过

- [ ] Task 7: 编写 E2E 测试套件（覆盖 6 个场景）
  - [ ] SubTask 7.1: 搭建 E2E 编排层（真实 RCAState 状态机 + mock 外部依赖隔离）
  - [ ] SubTask 7.2: 编写双闸门全链路 E2E（E2E-1 e2e_crag_hil_full_gate，CRAG→HIL→confirm→A5）
  - [ ] SubTask 7.3: 编写飞轮回写全链路 E2E（E2E-2 e2e_flywheel_writeback，验证 SIMILAR_TO 边创建与社区摘要更新）
  - [ ] SubTask 7.4: 编写 HIL 人机闭环 E2E（E2E-3 e2e_hil_human_loop，modify 回退 A4 重新生成）
  - [ ] SubTask 7.5: 编写 CRAG 重写循环 E2E（E2E-4 e2e_crag_rewrite_loop，max_rewrite_rounds=3）
  - [ ] SubTask 7.6: 编写飞轮复用 E2E（E2E-5 e2e_flywheel_reuse，历史根因命中置信提升）
  - [ ] SubTask 7.7: 编写高置信直通 E2E（E2E-6 e2e_high_confidence_bypass，跳过 HIL 直达 A5）
  - **验证**: 6 个场景全部通过；飞轮回写 E2E 验证 SIMILAR_TO 边创建；飞轮复用 E2E 验证历史根因命中

- [ ] Task 8: 编写跨模块集成测试套件（覆盖 6 个集成场景）
  - [ ] SubTask 8.1: 搭建集成测试骨架，在 conftest.py 注册 Fixture 工厂与 Mock 注册器（τ=0.6、max_rewrite_rounds=3、dedup_cosine_threshold=0.95）
  - [ ] SubTask 8.2: 编写 integ_agent4_to_crag（A4 Top-3 → CRAG 三分类，验证 LightRAG reranker 检索与 verdict/score 映射）
  - [ ] SubTask 8.3: 编写 integ_crag_to_hil（CRAG ambiguous 收敛 + confidence<τ=0.6 → HIL_PENDING 触发，验证 CRAG→HIL 顺序）
  - [ ] SubTask 8.4: 编写 integ_hil_to_agent5（accept→A5 继续 / reject→回退 A4 / timeout 降级三路径，验证状态机迁移）
  - [ ] SubTask 8.5: 编写 integ_agent5_to_flywheel（A5 Solution → FlywheelPayload 五字段提取，验证 root_cause/function/path/patch/case 映射）
  - [ ] SubTask 8.6: 编写 integ_flywheel_to_lightrag_ainsert（向量化→ainsert→SIMILAR_TO 边→社区摘要，验证调用参数与边创建）
  - [ ] SubTask 8.7: 编写 integ_full_pipeline_gate_flywheel（A4→CRAG→HIL→A5→飞轮回写→后续工单复用全链路，验证 gate_state 贯穿与 SIMILAR_TO 命中）
  - [ ] SubTask 8.8: 编写上下游依赖关系表断言，验证模块间调用顺序与数据结构流转（CragTriage/HilDecision/FlywheelPayload/SimilarToEdge 边界契约）
  - **验证**: 6 个集成场景全部通过；crag_gate/hil_gate/flywheel_writeback 真实执行且业务逻辑正确；mock 外部依赖不产生真实调用

- [ ] Task 9: 搭建测试数据与 Mock 基础设施（Fixture 工厂 + Mock 注册 + HIL 回调模拟器）
  - [ ] SubTask 9.1: 建立 Fixture 工厂（make_crag_triage/make_hil_decision/make_flywheel_payload/make_similar_edge/make_solution），支持参数化变体
  - [ ] SubTask 9.2: 落盘 tests/fixtures/gate/ 目录（crag/hil/flywheel 子目录 + gate_config.json + init_db.sql），共 13 个 JSON 样本
  - [ ] SubTask 9.3: 实现 Mock 注册器（mock_lightrag_reranker 三分类 / mock_lightrag_ainsert 参数验证 / mock_bge_m3 dim=1024 / mock_agent_a4 / mock_agent_a5）
  - [ ] SubTask 9.4: 实现 HIL 回调模拟器（accept/reject/timeout 三路径决策注入 + SSE 回调验证，模拟人工决策回调）
  - [ ] SubTask 9.5: 实现测试数据库初始化（内存 dict mock LightRAG Postgres 优先 / testcontainers 次选），预置历史根因与 SIMILAR_TO 边基线（≥10 条）
  - [ ] SubTask 9.6: 实现 load_fixture(path) 加载器与 @pytest.mark.parametrize 阈值边界矩阵（0.59/0.60/0.61 + 三档 verdict + 三路径决策）
  - **验证**: 7 类 Mock 样本（CragTriage/HilDecision/FlywheelPayload/SimilarToEdge/Solution/embedding/τ配置）就绪；Fixture 文件组织符合 tests/fixtures/gate/ 约定；mock 不产生真实外部调用；HIL 回调模拟器三路径覆盖

# Tasks

- [ ] Task 1: 定义 RCAState 数据结构与 LangGraph 状态机
  - [ ] SubTask 1.1: 定义 RCAState Pydantic 模型（bug_info/symptoms/error_type/query/suspect_services/S_static/P_runtime/top3/gate_status/solution/stage 共 11 字段）
  - [ ] SubTask 1.2: 定义子模型 BugInfo/SuspectFunction/AnomalyPath/RootCause/GateStatus/Solution 及 Stage 枚举
  - [ ] SubTask 1.3: 实现 LangGraph 状态机拓扑 START→A1→(A2∥A3 fan-out)→A4 fan-in→CRAG→HIL→A5→END
  - [ ] SubTask 1.4: 实现 A2/A3 并行 fan-out 节点与 A4 fan-in 汇聚节点（条件边 + join）
  - [ ] SubTask 1.5: 接入 Celery 异步调度（broker/result_backend 指向 Redis，task_default_queue=rca_pipeline）
  - [ ] SubTask 1.6: 实现 Redis 状态持久化（RCAState JSON 序列化 + state_key_prefix=rca:state: + TTL 86400s）
  - [ ] SubTask 1.7: 实现断点续跑恢复逻辑（重启后按 stage 字段跳过已完成阶段）
  - [ ] SubTask 1.8: 实现配额受限降级模式（degraded_dimensions 标注 + 可用维度兜底产出 Top-3）
  - [ ] SubTask 1.9: 实现整体异常捕获（未捕获异常 → stage=FAILED → SSE error 事件 → task failed）
  - 验证: 单元测试覆盖状态机拓扑断言、fan-out/fan-in 汇聚时序、断点续跑恢复路径、降级标注字段、异常捕获兜底

- [ ] Task 2: 实现 A1 问题理解 Agent
  - [ ] SubTask 2.1: 实现云捷 Bug 单 Adapter（拉单 + 鉴权 + 404/超时处理）
  - [ ] SubTask 2.2: 实现 LLM 抽取 symptoms/error_type/query/suspect_services 四字段
  - [ ] SubTask 2.3: Bug 单拉取失败时回退 bug_desc 文本抽取，二者皆空返回 A1_BUG_FETCH_ERROR
  - [ ] SubTask 2.4: 嫌疑服务为空时按报错栈包名/service 名模糊匹配 CMDB 扩大召回
  - [ ] SubTask 2.5: 报错栈缺失场景处理（error_type=unknown，A2 以入口函数为根）
  - [ ] SubTask 2.6: 输出 A1Output（symptoms/error_type/query/suspect_services）写入 RCAState
  - 验证: 输入真实 Bug 单与纯描述两种样本，校验四字段齐全且非空率 ≥ 90%；拉取失败回退路径单测通过

- [ ] Task 3: 实现 A2 代码分析 Agent
  - [ ] SubTask 3.1: opencode 拉码（repo+branch）并触发 CodeGraph 构建（对接 build-codegraph-knowledge-graph）
  - [ ] SubTask 3.2: 函数中文大纲生成（对接 generate-code-chinese-outline）
  - [ ] SubTask 3.3: 沿报错栈做静态污点追踪，在 CodeGraph 调用图上反向 BFS
  - [ ] SubTask 3.4: 输出 S_static（func_id/func_name/call_path/static_depth）
  - [ ] SubTask 3.5: CodeGraph 构建失败降级为 ripgrep 正则定位（标注 A2_GRAPH_DEGRADED）
  - [ ] SubTask 3.6: 报错栈帧无法映射时沿栈帧上层找最近可达函数替代起点
  - [ ] SubTask 3.7: 空仓库场景处理（S_static 置空 + A2_REPO_EMPTY）
  - 验证: 注入含/不含报错栈样本，校验 S_static 调用路径深度合理；模拟 CodeGraph 超时验证降级标注正确

- [ ] Task 4: 实现 A3 链路分析 Agent
  - [ ] SubTask 4.1: 实现 Trace Adapter（按时间窗拉 Jaeger/SkyWalking）
  - [ ] SubTask 4.2: 重建 span 树并识别异常 span（延迟/错误率阈值）
  - [ ] SubTask 4.3: 识别异常传播路径并映射到函数列表
  - [ ] SubTask 4.4: 输出 P_runtime（span_tree/propagation_path/functions/runtime_anomaly）
  - [ ] SubTask 4.5: Trace 拉取失败降级（P_runtime 置空 + A3_TRACE_MISSING）
  - [ ] SubTask 4.6: span 无法映射函数时保留 span 但 located_function 置空
  - 验证: 注入正常 Trace 与空时间窗样本，校验 P_runtime 异常路径映射正确；Trace 不可用时降级标注生效

- [ ] Task 5: 实现 A4 根因分析 Agent
  - [ ] SubTask 5.1: 四维关联交叉验证（S_static ∩ P_runtime.functions 交集，仅命中一维降权）
  - [ ] SubTask 5.2: 实现 score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency（权重 0.35/0.30/0.20/0.15）
  - [ ] SubTask 5.3: 按 score 降序剪枝保留 Top-3（TOP_K=3）
  - [ ] SubTask 5.4: 输出 Top-3 RootCause（root_cause/confidence/evidence_chain/located_function）
  - [ ] SubTask 5.5: 低置信触发 HIL（top_confidence < τ=0.6 → gate_status=HIL_PENDING）
  - [ ] SubTask 5.6: 候选集为空处理（top3=[] + A4_NO_CANDIDATE + 强制 HIL）
  - [ ] SubTask 5.7: 候选不足 3 时 insufficient_evidence 占位
  - 验证: 注入交集/非交集/空候选/不足 3 四组样本，校验 score 排序与占位逻辑；低置信阈值边界测试 τ=0.6

- [ ] Task 6: 实现 A5 方案生成 Agent
  - [ ] SubTask 6.1: LightRAG high-level 检索历史修复 + 最佳实践（对接 setup-lightrag-retrieval-engine）
  - [ ] SubTask 6.2: 生成补丁建议（patch_suggestion）
  - [ ] SubTask 6.3: 生成验证用例（test_cases）
  - [ ] SubTask 6.4: LightRAG 检索为空降级（historical_cases 置空 + A5_NO_HISTORY）
  - [ ] SubTask 6.5: 方案生成超时处理（部分返回 + A5_PARTIAL）
  - 验证: 注入有/无历史案例样本，校验补丁与用例非空；模拟超时验证部分返回标注正确

- [ ] Task 7: 实现根因 5 步标准化算法
  - [ ] SubTask 7.1: 步骤 1 定位范围（嫌疑服务 + CodeGraph 调用子图）
  - [ ] SubTask 7.2: 步骤 2 断言异常（Trace 异常 span + Metric 异常点）
  - [ ] SubTask 7.3: 步骤 3 挖掘关联（四维交集 + LightRAG 相似案例）
  - [ ] SubTask 7.4: 步骤 4 剪枝排序（置信度 score 排序保留 Top-3）
  - [ ] SubTask 7.5: 步骤 5 输出 Top-3（根因描述/置信度/证据链/定位函数）
  - [ ] SubTask 7.6: 5 段标准化报告封装（症状确认/链路分析/代码定位/根因确认/修复方案）
  - [ ] SubTask 7.7: 步骤产物缺失降级（step_degraded 标注 + 5 段结构完整保证）
  - 验证: 注入完整数据与 A3 缺失降级样本，校验 5 段结构完整且降级段标注正确

- [ ] Task 8: 集成双闸门与流水线端到端验证
  - [ ] SubTask 8.1: 在 A4→A5 间接入 CRAG 自动纠偏门（对接 build-dual-gate-flywheel）
  - [ ] SubTask 8.2: 实现 CRAG 三档处理（相关精炼/模糊补检/不相关改写 Query 重检索回灌 A4）
  - [ ] SubTask 8.3: 接入 HIL 人工确认门（低置信/CRAG 改写后挂起 + 前端确认面板）
  - [ ] SubTask 8.4: 实现 HIL 回灌 A4 重算 Top-3 逻辑
  - [ ] SubTask 8.5: 实现 HIL 驳回终止（task=rejected + 驳回原因入飞轮）
  - [ ] SubTask 8.6: 端到端跑通 6 阶段流水线，验证 Top-3 命中率（M2 ≥ 75%）
  - [ ] SubTask 8.7: SSE 流式阶段推送端到端验证（stage_start/stage_complete/gate_pending/gate_resolved/final/error 六类事件）
  - [ ] SubTask 8.8: 断连重连续传验证（Last-Event-ID 补发缺失事件）
  - 验证: 端到端注入真实 Bug 单，验证 6 阶段流水线跑通、SSE 六类事件齐全、Top-1 ≥ 80% / Top-3 ≥ 95% 命中率达标

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 1（以及 build-codegraph-knowledge-graph、generate-code-chinese-outline）
- Task 4 depends on Task 1
- Task 5 depends on Task 2、Task 3、Task 4（以及 implement-dual-graph-validation）
- Task 6 depends on Task 5（以及 setup-lightrag-retrieval-engine）
- Task 7 depends on Task 5
- Task 8 depends on Task 5、Task 6、Task 7（以及 build-dual-gate-flywheel）

- [ ] Task 9: 编写 UT 测试套件（覆盖上述 20 个用例，目标覆盖率 ≥85%）
  - [ ] SubTask 9.1: 搭建 pytest + pytest-asyncio 测试骨架与 conftest（fixture 注入 Celery eager、fakeredis、mock Agent 工厂）
  - [ ] SubTask 9.2: 编写模型层用例 test_rcastate_schema（11 字段必填、Stage 枚举、序列化/反序列化往返）
  - [ ] SubTask 9.3: 编写状态机用例 test_langgraph_topology + test_fanout_fanin（拓扑边断言、fan-out/fan-in 时序 ≤50ms、join 暂存不丢失）
  - [ ] SubTask 9.4: 编写 A1 用例 test_a1_bug_understanding + test_a1_fetch_fallback（mock 云捷 Adapter + LLM 抽取；404 回退 bug_desc；皆空返回 A1_BUG_FETCH_ERROR）
  - [ ] SubTask 9.5: 编写 A2 用例 test_a2_code_analysis + test_a2_graph_degraded（mock CodeGraph 调用图 + 反向 BFS；超时降级 ripgrep 标注 A2_GRAPH_DEGRADED）
  - [ ] SubTask 9.6: 编写 A3 用例 test_a3_trace_analysis + test_a3_trace_missing（mock Trace Adapter span 树；不可用降级标注 A3_TRACE_MISSING）
  - [ ] SubTask 9.7: 编写 A4 用例 test_a4_rootcause_score + test_a4_topk_pruning + test_a4_low_confidence_hil + test_a4_no_candidate（score 权重 0.35/0.30/0.20/0.15、Top-K=3、τ=0.6 边界 0.59/0.60/0.61、空候选强制 HIL）
  - [ ] SubTask 9.8: 编写调度与持久化用例 test_celery_async_dispatch + test_redis_state_persist + test_breakpoint_resume（eager 投递 rca_pipeline 队列、rca:state:{task_id} TTL=86400s、按 stage 跳过已完成阶段）
  - [ ] SubTask 9.9: 编写 SSE 与异常用例 test_sse_stream + test_degraded_mode + test_uncaught_exception（httpx AsyncClient 消费 stage_start/stage_complete/final、degraded_dimensions 标注、未捕获异常 stage=FAILED + SSE error）
  - [ ] SubTask 9.10: 编写 A5 用例 test_a5_solution_generation（mock LightRAG high-level 检索历史案例，校验 patch_suggestion/test_cases/historical_cases）
  - [ ] SubTask 9.11: 接入覆盖率工具（pytest-cov），line + branch 覆盖率 ≥ 85%，输出 XML/HTML 报告供 CI 采集
  - 验证: 20 个 UT 用例全部通过，line + branch 覆盖率 ≥ 85%，CI 可重复执行

- [ ] Task 10: 编写 E2E 测试套件（覆盖上述 8 个场景，使用 Celery eager + fakeredis + httpx）
  - [ ] SubTask 10.1: 搭建 E2E 测试基座（FastAPI TestClient + httpx AsyncClient + Celery eager + fakeredis 共享上下文，mock 全部上游）
  - [ ] SubTask 10.2: 编写 e2e_full_rca_pipeline（全链路 A1→A2∥A3→A4→CRAG→HIL→A5，断言 RCAState 11 字段流转、stage 终态、top3 长度=3）
  - [ ] SubTask 10.3: 编写 e2e_sse_progress_stream（httpx 订阅 SSE，断言 stage_start/stage_complete 序列 + final 含 top3+solution）
  - [ ] SubTask 10.4: 编写 e2e_hil_human_loop（低置信 → gate_pending → 人工回灌 → gate_resolved → A5 重生成）
  - [ ] SubTask 10.5: 编写 e2e_breakpoint_resume（A4 崩溃 → 重启续跑，断言 A1/A2/A3 不重跑、事件不重复）
  - [ ] SubTask 10.6: 编写 e2e_degraded_pipeline（CodeGraph 不可用 → ripgrep 兜底 → degraded_dimensions 含 codegraph → 仍产出 Top-3）
  - [ ] SubTask 10.7: 编写 e2e_no_trace_pipeline（Trace 不可用 → A3_TRACE_MISSING → score 跳过 runtime 维度 → 仍产出 Top-3）
  - [ ] SubTask 10.8: 编写 e2e_rest_api_analyze（POST /api/v1/rca/analyze → 202 + task_id 格式 rca-YYYYMMDD-NNNN → SSE final 完整）
  - [ ] SubTask 10.9: 编写 e2e_concurrent_analysis（并发 N 任务，断言 rca:state:{task_id} 隔离、SSE 按 task_id 路由、结果不串号）
  - [ ] SubTask 10.10: E2E 套件接入 CI（独立 stage 与 UT 隔离，固定 fakeredis 无真实上游依赖，可重复执行）
  - 验证: 8 个 E2E 场景全部通过，无真实上游依赖，CI 可重复执行

- [ ] Task 11: 编写跨模块集成测试套件（覆盖上述 7 个集成场景，使用 fakeredis + Celery eager + mock 全部上游）
  - [ ] SubTask 11.1: 搭建集成测试基座（conftest 注册 fakeredis + Celery eager + FastAPI TestClient + httpx AsyncClient 共享上下文，mock 全部上游依赖）
  - [ ] SubTask 11.2: 编写 integ_a1_bug_adapter（mock Bug单 Adapter 拉单+鉴权，mock LLM 四字段抽取，断言 A1Output 写入 RCAState）
  - [ ] SubTask 11.3: 编写 integ_a2_codegraph_code2cn（mock CodeGraph callers/callees + code2cn_outline，断言 S_static 调用路径深度与中文大纲一致）
  - [ ] SubTask 11.4: 编写 integ_a3_trace_cmdb（mock Trace Adapter span 树 + CMDB 服务拓扑，断言 P_runtime 异常路径映射）
  - [ ] SubTask 11.5: 编写 integ_a4_lightrag_dualgraph（mock LightRAG aquery + 双图谱 cross_validate，断言 score 计算与 Top-3 交集 boost）
  - [ ] SubTask 11.6: 编写 integ_a4_to_gate（注入低置信 Top-3 top_confidence=0.42，断言 confidence<τ=0.6 触发 HIL + gate_status=HIL_PENDING + SSE gate_pending 事件）
  - [ ] SubTask 11.7: 编写 integ_a5_to_flywheel_frontend（mock LightRAG 历史案例，断言知识飞轮回写被调用 + SSE final 推送 top3+solution）
  - [ ] SubTask 11.8: 编写 integ_full_pipeline_5agent（全链路 A1→A2∥A3→A4→CRAG→HIL→A5，断言 RCAState 11 字段流转 + 飞轮回写 + SSE + 无真实外部调用）
  - [ ] SubTask 11.9: 集成测试接入 CI（独立 stage 与 UT/E2E 隔离，固定 fakeredis 无真实上游依赖，可重复执行）
  - 验证: 7 个跨模块集成测试场景全部通过，mock 全部上游不产生真实外部调用，CI 可重复执行

- [ ] Task 12: 搭建测试数据与 Mock 基础设施（RCAState Fixture 工厂 + Agent I/O 样本 + Mock 注册 + fakeredis + Celery eager 配置）
  - [ ] SubTask 12.1: 实现 RCAState Fixture 工厂 make_rca_state(stage=...)，按 stage 预填产物 + 边界样本（低置信/空候选/降级）
  - [ ] SubTask 12.2: 编写 9 类 Mock 样本 JSON（RCAState 完整 / Bug单 / A1-A5Output / SSE 事件流 / LLM 响应 A1-A5 各一）
  - [ ] SubTask 12.3: 在 conftest.py 注册全局 fixture（celery_eager / fakeredis_client / mock_bug_adapter / mock_codegraph / mock_trace / mock_lightrag / mock_dualgraph / mock_llm / fastapi_client）
  - [ ] SubTask 12.4: 实现 Mock 规范（Bug单 Adapter 返回预设 JSON；CodeGraph MCP 返回 callers/callees/code2cn_outline；Trace Adapter 返回预设 span 树；LightRAG aquery 返回检索结果；双图谱 cross_validate 返回 Candidate 列表；LLM 按 Agent 角色返回响应）
  - [ ] SubTask 12.5: 配置 Celery eager（CELERY_TASK_ALWAYS_EAGER=True + CELERY_TASK_EAGER_PROPAGATES=True）+ broker/result_backend 指向 fakeredis
  - [ ] SubTask 12.6: 实现测试数据库初始化（fakeredis + RCAState JSON 序列化 + state_key_prefix=rca:state: + TTL=86400s + flushall 隔离）
  - [ ] SubTask 12.7: 组织 Fixture 文件目录 tests/fixtures/engine/{rca_state,agent_io,sse,llm}/，与 spec 数据契约字段名与类型严格一致
  - 验证: 9 类 Mock 样本 JSON 就绪；fakeredis + Celery eager 配置正确不依赖真实 Redis/RabbitMQ；Fixture 文件组织符合 tests/fixtures/engine/ 约定

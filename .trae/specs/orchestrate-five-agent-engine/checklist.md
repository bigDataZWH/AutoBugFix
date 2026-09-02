# Checklist

## 编排与状态机
- [ ] RCAState 数据结构定义完整，11 字段（bug_info/symptoms/error_type/query/suspect_services/S_static/P_runtime/top3/gate_status/solution/stage）全部可通过 Pydantic 校验
- [ ] LangGraph 状态机拓扑为 START→A1→(A2∥A3 fan-out)→A4 fan-in→CRAG→HIL→A5→END，单元测试断言拓扑节点与边数量正确
- [ ] A2/A3 并行 fan-out 验证：两分支启动时间差 ≤ 50ms，互不阻塞
- [ ] A4 fan-in 聚合验证：仅在 A2 与 A3 均完成后触发，提前到达的分支结果暂存不丢失
- [ ] Celery 异步调度已接入，broker/result_backend 指向 Redis，task_default_queue=rca_pipeline，task_time_limit=180s
- [ ] Redis 状态持久化验证：RCAState 序列化写入 rca:state:{task_id}，TTL=86400s，可正确反序列化
- [ ] 断点续跑验证：在 A3 阶段模拟进程崩溃，重启后从 A3 续跑，A1/A2 不重跑，恢复耗时 ≤ 2s
- [ ] 配额受限降级验证：模拟 LLM 限流，degraded_dimensions 含 "llm"，仍以可用维度产出 Top-3
- [ ] 整体异常捕获验证：注入未捕获异常后 stage=FAILED，SSE 推送 error 事件，task 状态为 failed

## A1 问题理解 Agent
- [ ] 云捷 Bug 单 Adapter 拉单成功，鉴权与 404/超时处理覆盖
- [ ] LLM 抽取 symptoms/error_type/query/suspect_services 四字段齐全，非空率 ≥ 90%
- [ ] Bug 单拉取失败回退：bug_desc 文本抽取路径单测通过；二者皆空返回 A1_BUG_FETCH_ERROR
- [ ] 嫌疑服务为空时 CMDB 模糊匹配扩大召回验证通过
- [ ] 报错栈缺失场景 error_type=unknown 标注正确，A2 以入口函数为根

## A2 代码分析 Agent
- [ ] opencode 拉码（repo+branch）成功，CodeGraph 自动构建对接 build-codegraph-knowledge-graph
- [ ] 函数中文大纲生成对接 generate-code-chinese-outline，大纲覆盖率 ≥ 80%
- [ ] 静态污点追踪：CodeGraph 调用图反向 BFS 输出 S_static，含 func_id/func_name/call_path/static_depth
- [ ] CodeGraph 构建失败降级：ripgrep 正则定位，标注 A2_GRAPH_DEGRADED，降级路径单测通过
- [ ] 报错栈帧无法映射时沿栈帧上层找最近可达函数，替代信息写入 evidence_chain
- [ ] 空仓库场景 S_static 置空 + A2_REPO_EMPTY 标注正确

## A3 链路分析 Agent
- [ ] Trace Adapter 按时间窗拉 Jaeger/SkyWalking 成功
- [ ] span 树重建正确，异常 span 识别（延迟/错误率阈值）命中
- [ ] 异常传播路径 P_runtime 输出 span_tree/propagation_path/functions/runtime_anomaly，函数映射正确
- [ ] Trace 拉取失败降级：P_runtime 置空 + A3_TRACE_MISSING，A4 三维兜底验证通过
- [ ] span 无法映射函数时 located_function 置空，A4 降权处理验证

## A4 根因分析 Agent
- [ ] 四维关联交叉验证：S_static ∩ P_runtime.functions 交集函数置信度 boost，仅命中一维降权
- [ ] score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency，权重 0.35/0.30/0.20/0.15
- [ ] 按 score 降序剪枝保留 Top-3（TOP_K=3），排序单测通过
- [ ] Top-3 每条含 root_cause/confidence/evidence_chain/located_function 四字段完整
- [ ] 低置信触发 HIL：top_confidence < τ=0.6 → gate_status=HIL_PENDING，边界值 0.59/0.60/0.61 测试通过
- [ ] 候选集为空：top3=[] + A4_NO_CANDIDATE + 强制 HIL 触发
- [ ] 候选不足 3 时 insufficient_evidence 占位，Top-3 数组长度恒为 3

## A5 方案生成 Agent
- [ ] LightRAG high-level 检索历史修复 + 最佳实践对接 setup-lightrag-retrieval-engine
- [ ] 补丁建议（patch_suggestion）与验证用例（test_cases）非空
- [ ] LightRAG 检索为空降级：historical_cases 置空 + A5_NO_HISTORY 标注
- [ ] 方案生成超时处理：部分返回 + A5_PARTIAL 标注，允许人工补全

## 双闸门机制
- [ ] CRAG 门位于 A4→A5 之间，对接 build-dual-gate-flywheel
- [ ] CRAG 三档处理验证：相关精炼 / 模糊补检 / 不相关改写 Query 重检索回灌 A4
- [ ] HIL 门挂起验证：gate_status=HIL_PENDING，前端确认面板推送
- [ ] HIL 回灌 A4 重算 Top-3 逻辑验证通过
- [ ] HIL 驳回终止：task=rejected，驳回原因入知识飞轮

## 根因 5 步标准化算法
- [ ] 5 步流程完整：定位范围→断言异常→挖掘关联→剪枝排序→输出 Top-3
- [ ] 5 段标准化报告齐全：症状确认/链路分析/代码定位/根因确认/修复方案
- [ ] 步骤产物缺失降级：step_degraded 标注 + 5 段结构完整保证验证

## SSE 流式推送
- [ ] POST /api/v1/rca/analyze 接口返回 task_id（202 Accepted）
- [ ] GET /api/v1/rca/{task_id}/stream SSE 六类事件齐全：stage_start/stage_complete/gate_pending/gate_resolved/final/error
- [ ] 断连重连续传：客户端携带 Last-Event-ID，服务端从 Redis 事件流补发缺失事件，不丢阶段

## 验收指标（量化达标）
- [ ] Top-1 根因命中率 ≥ 80%
- [ ] Top-3 覆盖率 ≥ 95%
- [ ] P95 端到端响应 ≤ 12s
- [ ] 可用性 ≥ 99.5%
- [ ] M1：端到端 < 30s
- [ ] M2：Top-3 命中率 ≥ 75%
- [ ] 误报率（双闸门后）≤ 5%

## 测试质量保障
- [ ] UT 覆盖率 ≥85%（line + branch）
- [ ] 20 个 UT 用例全部通过
- [ ] 8 个 E2E 场景全部通过
- [ ] LangGraph 拓扑断言 UT 通过（含 fan-out/fan-in 时序）
- [ ] score 四维权重计算 UT 通过（0.35/0.30/0.20/0.15）
- [ ] HIL 阈值 τ=0.6 触发逻辑 UT 通过
- [ ] 断点续跑 E2E 验证恢复正确
- [ ] SSE 流式推送 E2E 验证事件序列完整
- [ ] 降级模式 E2E 验证兜底产出 Top-3
- [ ] 并发分析 E2E 验证 state_key 隔离
- [ ] CI 流水线集成 pytest 执行

## 跨模块集成测试与测试数据 Mock
- [ ] 7 个跨模块集成测试场景全部通过
- [ ] RCAState/Bug单/A1-A5Output/SSE/LLM 9 类 Mock 样本 JSON 就绪
- [ ] fakeredis + Celery eager 配置正确（不依赖真实 Redis/RabbitMQ）
- [ ] 全链路集成测试验证 RCAState 11 字段流转正确
- [ ] LLM mock 按 Agent 角色返回正确预设响应
- [ ] SSE 事件流序列集成测试通过
- [ ] Fixture 文件组织符合 tests/fixtures/engine/ 约定
- [ ] mock 全部上游依赖（Bug单/CodeGraph/Trace/LightRAG/双图谱/LLM）不产生真实外部调用

# Tasks

## Task 1: 实现服务级拓扑图构建
- [ ] SubTask 1.1: 设计服务级拓扑数据模型（节点=微服务/接口，边=跨服务调用），定义统一节点 ID 命名空间
- [ ] SubTask 1.2: 实现 Trace span 聚合为服务节点与跨服务调用边
- [ ] SubTask 1.3: 接入 CMDB 补全服务元数据（服务名/归属/负责人）
- [ ] SubTask 1.4: 实现根因传播遍历算法（沿调用边反向传播）
- [ ] SubTask 1.5: 实现 Trace 缺失/CMDB 缺失降级兜底（标注降级标记）
- **验证步骤**: 构造完整 Trace+CMDB 样例验证拓扑可遍历；构造 Trace 缺失样例验证 CMDB 兜底与降级标记；构造双缺样例验证降级告警触发

## Task 2: 接入函数级调用图（复用 build-codegraph-knowledge-graph）
- [ ] SubTask 2.1: 对接 CodeGraph 函数级调用图数据加载器
- [ ] SubTask 2.2: 暴露函数节点/调用边/继承/引用查询 API
- [ ] SubTask 2.3: 实现反向 BFS 产出 S_static（含 call_path、static_depth）
- [ ] SubTask 2.4: 实现增量构建 + 缓存预热，避免大仓库阻塞流水线
- [ ] SubTask 2.5: 实现跨语言调用链桥接节点与"跨语言断点"标注
- **验证步骤**: 验证 S_static 含 function_id/call_path/static_depth 三字段；大仓库样例验证增量构建命中缓存；跨语言样例验证桥接节点标注

## Task 3: 实现跨层 CONTAINS 关系映射
- [ ] SubTask 3.1: 设计 `service::function` 复合键命名空间
- [ ] SubTask 3.2: 建立服务级节点 → 函数级节点的 CONTAINS 下钻边
- [ ] SubTask 3.3: 实现服务→函数下钻查询接口
- [ ] SubTask 3.4: 实现 CONTAINS 缺失时触发 CodeGraph 增量补建兜底
- **验证步骤**: 嫌疑服务下钻返回全部函数子图；CONTAINS 缺失样例验证增量补建与"函数下钻不可用"标注

## Task 4: 实现静态×运行时交叉验证算法
- [ ] SubTask 4.1: 实现 `cross_validate(S_static, P_runtime, metric_anomalies, change_records)` 主入口
- [ ] SubTask 4.2: 实现 `functions_of(P_runtime)` 辅助函数，返回异常路径函数 ID 集合
- [ ] SubTask 4.3: 计算 `S_static ∩ functions_of(P_runtime)` 交集，交集命中为高置信候选
- [ ] SubTask 4.4: 实现 `score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency` 四维加权
- [ ] SubTask 4.5: 实现按 score 降序取 Top-3 输出 List[Candidate]（含 evidence 四维证据）
- [ ] SubTask 4.6: 实现单路命中降级（不进入高置信候选，仅 Top-3 不足时补位标注）
- [ ] SubTask 4.7: 实现 Metric 缺失/变更记录缺失时权重归零 + 按比例重分配降级
- [ ] SubTask 4.8: 实现交集为空时降级策略（单路补位 + 上浮 HIL 闸门）
- **验证步骤**: 交集命中样例验证 Top-3 排序正确；单路命中样例验证降级；Metric/变更缺失样例验证权重重分配；交集为空样例验证 HIL 上浮

## Task 5: 集成到 A4 根因分析并验证误报压低
- [ ] SubTask 5.1: 交叉验证结论接入 A4 Top-3 输出（对接 orchestrate-five-agent-engine）
- [ ] SubTask 5.2: Candidate 证据链对接双闸门（build-dual-gate-flywheel：CRAG 门 + HIL 门）
- [ ] SubTask 5.3: 接入权重 w1-w4 配置项（支持环境覆盖）
- [ ] SubTask 5.4: 构造评估集，跑批 V2.0 vs V3.0 对比
- [ ] SubTask 5.5: 统计并验证误报率下降 ≥ 40%、双闸门后误报率 ≤ 5%、Top-3 覆盖率 ≥ 95%、Top-1 命中率 ≥ 80%
- **验证步骤**: 评估集跑批输出四项 KPI 达标报告；双闸门放行后误报率复测；权重可配置性验证

## Task 6: 编写 UT 测试套件（覆盖 14 个用例，目标覆盖率 ≥85%）
- [ ] SubTask 6.1: 搭建 pytest + pytest-asyncio 骨架与 fixtures（mock 数据工厂、YAML 配置工厂、tmp_path 隔离）
- [ ] SubTask 6.2: 编写数据结构 Schema 用例（test_s_static_schema / test_p_runtime_schema / test_candidate_schema）
- [ ] SubTask 6.3: 编写交叉验证核心用例（test_cross_validate_intersection / test_contains_relationship / test_topk_ranking / test_empty_intersection）
- [ ] SubTask 6.4: 编写 score 公式与权重用例（test_score_formula / test_weight_config_load / test_degradation_switch）
- [ ] SubTask 6.5: 编写降级与证据链用例（test_single_dimension_degrade / test_both_dimensions_boost / test_evidence_chain_construction / test_metric_correlation）
- [ ] SubTask 6.6: mock Adapter 封装（CMDB Adapter mock / Trace Adapter mock / CodeGraph 加载器 mock）
- [ ] SubTask 6.7: 接入 pytest-cov 覆盖率统计，目标 line + branch ≥85%，产出覆盖率报告
- **验证步骤**: 14 个 UT 用例全部通过；覆盖率报告 line+branch ≥85%；CI 集成 pytest 执行

## Task 7: 编写 E2E 测试套件（覆盖 6 个场景）
- [ ] SubTask 7.1: 搭建 E2E 骨架与端到端 fixtures（双图谱 mock 数据集、评估样本集、断言工具）
- [ ] SubTask 7.2: 编写全链路场景 e2e_dual_graph_build_validate
- [ ] SubTask 7.3: 编写降级场景 e2e_static_only_degraded / e2e_runtime_only_degraded
- [ ] SubTask 7.4: 编写交集场景 e2e_full_intersection / e2e_empty_intersection_fallback
- [ ] SubTask 7.5: 编写权重调优场景 e2e_weight_tuning（YAML 热加载对比）
- [ ] SubTask 7.6: 构造 V2.0 vs V3.0 评估集，验证假阳性降低 ≥40%
- [ ] SubTask 7.7: E2E 断言工具封装（Top-3 结构断言、evidence 四维断言、降级标记断言）
- **验证步骤**: 6 个 E2E 场景全部通过；假阳性降低 ≥40% 复测通过；E2E 报告产出

# Task Dependencies
- Task 2 depends on build-codegraph-knowledge-graph（函数级调用图数据源）
- Task 3 depends on Task 1、Task 2
- Task 4 depends on Task 3（以及 orchestrate-five-agent-engine 的 A2/A3 输出）
- Task 5 depends on Task 4（以及 orchestrate-five-agent-engine、build-dual-gate-flywheel）
- Task 6 depends on Task 4（交叉验证算法实现完成）
- Task 7 depends on Task 6（UT 套件就绪）+ Task 5（A4 集成完成）

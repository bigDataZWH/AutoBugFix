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

# Task Dependencies
- Task 2 depends on build-codegraph-knowledge-graph（函数级调用图数据源）
- Task 3 depends on Task 1、Task 2
- Task 4 depends on Task 3（以及 orchestrate-five-agent-engine 的 A2/A3 输出）
- Task 5 depends on Task 4（以及 orchestrate-five-agent-engine、build-dual-gate-flywheel）

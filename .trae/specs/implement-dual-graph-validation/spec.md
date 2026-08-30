# 双图谱交叉验证 Spec

## Why
单图谱（仅静态或仅运行时）误报率高：静态调用图可达但运行时未必异常，运行时异常未必能解释根因。核心创新"双图谱交叉验证"——CodeGraph 静态调用图 ∩ Trace 异常传播路径的交集函数置信度最高（既静态可达又运行时确实异常），从机制上压低误报。双图谱 = 服务级拓扑图（Trace + CMDB 聚合）+ 函数级调用图（CodeGraph 自动构建），跨层 CONTAINS 关系实现服务→函数下钻，使根因定位从概率式猜测升级为可验证的工程化推理。

## What Changes
- **新增服务级拓扑图构建模块**：基于 Trace + CMDB 聚合，节点粒度为微服务/接口，边为跨服务调用关系，用于跨服务根因传播。BREAKING：服务拓扑数据模型从原扁平服务列表升级为图结构（节点 + 边）。
- **新增函数级调用图接入**：复用 build-codegraph-knowledge-graph 产物，CodeGraph（opencode 插件自动构建）产出函数/方法粒度调用图，边含调用/继承/引用关系。BREAKING：A4 输入从"嫌疑函数列表"升级为"带 call_path 的函数子图"。
- **新增跨层 CONTAINS 关系映射层**：服务级拓扑节点 → 函数级调用图节点的下钻边，实现服务→函数定位下钻。BREAKING：引入双层图谱统一节点 ID 命名空间（`service::function` 复合键）。
- **新增静态×运行时交叉验证算法**：`cross_validate(S_static, P_runtime, metric_anomalies, change_records)` 计算 `S_static ∩ functions_of(P_runtime)` 交集，命中函数为高置信候选，单路命中降级。BREAKING：A4 根因产出从"单路检索 Top-N"改为"双路交集 Top-3"。
- **新增四维 score 计算器**：`score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency`，权重 w1-w4 可配置，候选按 score 降序取 Top-3，每条含四维 evidence 证据链。

## Impact
- **Affected specs**: `build-codegraph-knowledge-graph`（函数级调用图数据源）、`orchestrate-five-agent-engine`（A4 根因分析核心算法、A2/A3 输出消费方）、`setup-lightrag-retrieval-engine`（交叉验证可复用 LightRAG 相似案例）、`build-dual-gate-flywheel`（双闸门依赖交叉验证结论与证据链）
- **Affected code**: 双图谱构建模块（服务级拓扑图构建器、函数级调用图加载器）、CONTAINS 关系映射层、交叉验证算法实现（`cross_validate`）、四维 score 计算器、A4 Top-3 输出适配层、权重配置项（w1-w4）

## ADDED Requirements

### Requirement: 服务级拓扑图构建
系统 SHALL 基于 Trace + CMDB 聚合构建服务级拓扑图，节点粒度为微服务/接口，边为跨服务调用关系，用于跨服务根因传播分析。

#### Scenario: 服务级拓扑正常构建
- **WHEN** A3 拉取 Trace span 树与 CMDB 服务元数据且数据完整
- **THEN** 聚合生成服务级拓扑图，节点为微服务/接口，边为跨服务调用关系，可遍历进行根因传播

#### Scenario: 跨服务根因传播
- **WHEN** 异常 span 定位到入口服务并沿调用边反向传播
- **THEN** 沿服务级拓扑边传播根因，输出嫌疑服务链路，供 CONTAINS 下钻

#### Scenario: Trace 数据缺失降级（边缘）
- **WHEN** Trace span 不完整或缺失导致无法聚合跨服务调用边
- **THEN** 以 CMDB 静态服务依赖关系补全拓扑边，并在证据链标注"Trace 缺失·CMDB 兜底"，降低该路径置信度

#### Scenario: CMDB 元数据缺失降级（边缘）
- **WHEN** CMDB 服务元数据缺失导致节点服务名/归属不明
- **THEN** 以 Trace span service 字段作为节点标识兜底，并在证据链标注降级标记

#### Scenario: 服务级拓扑构建失败（错误）
- **WHEN** Trace 与 CMDB 双双缺失导致拓扑图无法构建
- **THEN** 交叉验证降级为仅函数级单路，记录降级告警，A4 标注"服务级拓扑不可用"

### Requirement: 函数级调用图构建
系统 SHALL 经 CodeGraph（opencode 插件）自动构建函数级调用图，节点粒度为函数/方法，边含调用/继承/引用关系，用于精确到函数的根因定位。

#### Scenario: 函数级调用图就绪
- **WHEN** A2 触发 CodeGraph 构建
- **THEN** 函数级调用图就绪，节点为函数/方法，边为调用/继承/引用关系，可反向 BFS 产出 S_static

#### Scenario: 大仓库增量构建
- **WHEN** 嫌疑代码仓库规模较大导致全量构建耗时
- **THEN** 启用增量构建 + 缓存预热，仅重建变更函数子图，保障构建不阻塞流水线

#### Scenario: 跨语言调用链断裂（错误）
- **WHEN** 调用链跨语言（如 Python → Go 跨服务）导致调用图断裂
- **THEN** 插入桥接节点并标注"跨语言断点"，人工标注回流修复，避免定位偏差

#### Scenario: CodeGraph 构建超时降级（错误）
- **WHEN** CodeGraph 构建超时未就绪
- **THEN** 降级使用调用图缓存，若无缓存则仅运行时单路，记录降级告警

### Requirement: 跨层 CONTAINS 关系
系统 SHALL 在服务级拓扑图节点与函数级调用图节点之间建立跨层 CONTAINS 关系，实现服务→函数下钻。

#### Scenario: 服务→函数下钻
- **WHEN** 根因传播定位到某嫌疑服务
- **THEN** 经 CONTAINS 关系下钻到该服务包含的函数级调用子图，精确定位嫌疑函数

#### Scenario: 多函数子图下钻
- **WHEN** 嫌疑服务 CONTAINS 多个函数节点
- **THEN** 下钻返回该服务全部函数子图，交由交叉验证按 score 排序筛 Top-3

#### Scenario: CONTAINS 关系缺失（边缘）
- **WHEN** 服务节点未映射函数子图导致 CONTAINS 边缺失
- **THEN** 触发 CodeGraph 对该服务代码增量补建，缺失期间降级为服务级结论并标注"函数下钻不可用"

### Requirement: 静态×运行时交叉验证算法
系统 SHALL 对 A2 输出的静态嫌疑函数集 S_static 与 A3 输出的运行时异常路径 P_runtime 做交集，仅当函数同时出现在 S_static 且属于 functions_of(P_runtime) 时作为高置信候选，并计算 `score = w1*static_depth + w2*runtime_anomaly + w3*metric_corr + w4*change_recency`，按 score 降序取 Top-3。

#### Scenario: 交集命中高置信
- **WHEN** 函数 f 既静态可达（在 S_static）又运行时异常（在 functions_of(P_runtime)）
- **THEN** f 进入候选集，按 score 排序，取 Top-3 作为根因

#### Scenario: 单路命中降级
- **WHEN** 函数仅出现在静态（S_static）或运行时（P_runtime）单路
- **THEN** 不作为高置信候选，避免误报；仅当 Top-3 不足时按降级策略补位并标注单路来源

#### Scenario: 四维 score 计算
- **WHEN** 交集命中函数 f
- **THEN** 取 f 的 static_depth、runtime_anomaly、metric_corr、change_recency 四维证据，加权求和得 score，写入 Candidate.evidence

#### Scenario: Top-3 输出
- **WHEN** 候选集完成 score 计算
- **THEN** 按 score 降序排序取前 3，输出 List[Candidate]，每条含 function_id/score/evidence

#### Scenario: Metric 缺失降级（边缘）
- **WHEN** metric_anomalies 缺失导致 metric_corr 维度不可用
- **THEN** 将 w3 权重归零并按比例重分配至 w1/w2/w4，在 evidence 标注"Metric 维度缺失"，候选仍可产出

#### Scenario: 变更记录缺失降级（边缘）
- **WHEN** change_records 缺失导致 change_recency 维度不可用
- **THEN** 将 w4 权重归零并按比例重分配，在 evidence 标注"变更记录缺失"，候选仍可产出

#### Scenario: 交集为空（错误）
- **WHEN** S_static ∩ functions_of(P_runtime) 为空
- **THEN** 触发降级策略：以单路命中补位并标注"双路未收敛"，置信度降级，必要时上浮 HIL 闸门

### Requirement: 四维关联与误报压低
系统 SHALL 通过双图谱交叉验证机制，使根因定位从概率式猜测升级为可验证的工程化推理，目标双闸门后误报率 ≤ 5%，相比 V2.0 误报率下降 ≥ 40%，Top-3 覆盖率 ≥ 95%，Top-1 命中率 ≥ 80%。

#### Scenario: 误报压低验证
- **WHEN** 交叉验证产出 Top-3
- **THEN** 误报率显著低于单检索范式（V2.0），实现误报率下降 ≥ 40%

#### Scenario: V2.0 对比
- **WHEN** 同一批故障样本分别在 V2.0 单检索与 V3.0 双图谱下跑批
- **THEN** V3.0 在检索范式（单路 → 双路交叉）、定位深度（文件/函数 → 函数+调用路径+数据流）、幻觉控制（提示词 → 双闸门强制收敛）、可解释性（单证据链 → 结构+语义双证据链）四维均优于 V2.0

#### Scenario: 双闸门后误报率
- **WHEN** Top-3 经 CRAG 自动纠偏门 + HIL 人工确认门双闸门放行
- **THEN** 双闸门后误报率 ≤ 5%

#### Scenario: Top-3 覆盖率
- **WHEN** 在评估集上统计 Top-3 是否覆盖真根因
- **THEN** Top-3 覆盖率 ≥ 95%

#### Scenario: Top-1 命中率
- **WHEN** 在评估集上统计 Top-1 是否命中真根因
- **THEN** Top-1 命中率 ≥ 80%

## 技术细节

### 接口定义
交叉验证主入口：输入 A2 静态嫌疑函数集、A3 运行时异常路径、Metric 异常、变更记录，输出按 score 降序的 Top-3 候选。

```python
def cross_validate(
    S_static: List[Function],
    P_runtime: PropagationPath,
    metric_anomalies: MetricAnomalies,
    change_records: ChangeRecords,
) -> List[Candidate]:
    candidates = []
    for f in S_static:
        if f.function_id in functions_of(P_runtime):
            score = (
                w1 * f.static_depth
                + w2 * runtime_anomaly(f, P_runtime)
                + w3 * metric_corr(f, metric_anomalies)
                + w4 * change_recency(f, change_records)
            )
            candidates.append(Candidate(
                function_id=f.function_id,
                score=score,
                evidence=Evidence(
                    static_depth=f.static_depth,
                    runtime_anomaly=runtime_anomaly(f, P_runtime),
                    metric_corr=metric_corr(f, metric_anomalies),
                    change_recency=change_recency(f, change_records),
                ),
            ))
    top3 = sorted(candidates, key=lambda c: c.score, reverse=True)[:3]
    return top3
```

辅助函数 `functions_of(P_runtime)` 返回 P_runtime 异常路径上的函数 ID 集合，用于交集判定；交集命中（既在 S_static 又在 functions_of(P_runtime)）为高置信候选，单路命中降级不进入高置信候选。

### 数据结构
S_static（A2 输出静态嫌疑函数集，CodeGraph 调用图反向 BFS 产出）：

```python
S_static_item = {
    "function_id": str,
    "call_path": List[str],
    "static_depth": int,
}
S_static = List[S_static_item]
```

P_runtime（A3 输出运行时异常路径，Trace span 映射到函数）：

```python
P_runtime = {
    "span_tree": SpanTree,
    "anomaly_path": List[str],
    "functions": List[str],
}
```

Candidate（交叉验证输出候选）：

```python
Candidate = {
    "function_id": str,
    "score": float,
    "evidence": {
        "static_depth": int,
        "runtime_anomaly": float,
        "metric_corr": float,
        "change_recency": float,
    },
}
```

score 公式：

```
score = w1 * static_depth + w2 * runtime_anomaly + w3 * metric_corr + w4 * change_recency
```

### 配置项
权重 w1-w4 可调，默认经验值如下，支持按环境覆盖；维度缺失时对应权重归零并按比例重分配。

```yaml
dual_graph_validation:
  weights:
    w1_static_depth: 0.3
    w2_runtime_anomaly: 0.3
    w3_metric_corr: 0.2
    w4_change_recency: 0.2
  top_k: 3
  degradation:
    single_path_allowed: true
    metric_missing_rebalance: true
    change_missing_rebalance: true
```

双图谱构成对照：

| 图谱 | 粒度 | 构建 | 用途 |
| --- | --- | --- | --- |
| 服务级拓扑图 | 微服务/接口 | Trace + CMDB 聚合 | 跨服务根因传播 |
| 函数级调用图 | 函数/方法 | CodeGraph（opencode 插件,自动） | 精确到函数定位 |

跨层 CONTAINS 关系：服务级拓扑节点 ──CONTAINS──► 函数级调用图节点，实现服务→函数下钻。

## 验收指标
- 误报率下降 ≥ 40%（vs V2.0 单检索范式）
- 双闸门后误报率 ≤ 5%
- Top-3 覆盖率 ≥ 95%
- Top-1 命中率 ≥ 80%
- 交叉验证产出 Top-3，每条含 function_id/score/evidence 四维证据
- 交集命中为高置信候选，单路命中降级不进入高置信候选
- V2.0 → V3.0 跃迁四维均优于 V2.0：检索范式（单路→双路交叉）、定位深度（文件/函数→函数+调用路径+数据流）、幻觉控制（提示词→双闸门强制收敛）、可解释性（单证据链→结构+语义双证据链）

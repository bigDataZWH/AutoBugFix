# RCA Command v3 · 端到端（E2E）测试报告

## 1. 测试概述

| 项目 | 内容 |
|------|------|
| 被测系统 | RCA Command · 问题单智能根因分析平台 v3.0.0 |
| 测试类型 | 端到端（E2E）功能测试 + 单元回归测试 + 真实联调（online_full）+ 质量评估 |
| 测试模式 | **online_full（完整联调）**：Postgres / Redis / LightRAG / CodeGraph / opencode LLM 全真实组件 |
| 测试日期 | 2026-08-31 |
| 测试环境 | Linux 沙箱（CI=true），Python 3.14.7，FastAPI 0.115.6，uvicorn 0.34.0 |
| 中间件 | Postgres 16 + pgvector 0.6.0，Redis 7.0.15，LightRAG 1.5.6（hku），opencode CLI 1.18.25（免费模型） |
| 服务地址 | http://localhost:8000 |
| 结论 | **通过（PASS）** |

## 2. 系统架构

```
L6 交互层    rca-command.html（单页Web界面，v3重构版）
L5 编排层    FastAPI + 5-Agent引擎编排器（engine.py）
L4 智能引擎  A1问题理解 → A2静态分析 ∥ A3链路分析 → A4根因定位 → CRAG闸门 → HIL闸门 → A5方案生成
L3 检索层    LightRAG三路检索（history/propagation/architecture）
L2 图谱层    CodeGraph代码图谱 + 服务级拓扑（service_topology.py）
L1 数据层    Postgres 16 + pgvector（向量/键值/图谱存储）+ Redis 7（缓存/状态）
```

## 3. 真实联调环境准备

### 3.1 基础设施

| 组件 | 版本 | 状态 |
|------|------|------|
| PostgreSQL | 16 + pgvector 0.6.0 | 运行中，库 `rca`（vector/pg_trgm/pgcrypto 扩展就绪） |
| Redis | 7.0.15 | 运行中（PING → PONG） |
| opencode CLI | 1.18.25 | 就绪，免费模型 `opencode/nemotron-3.5-lightning-free` 可用 |
| LightRAG | 1.5.6（lightrag-hku） | 已适配并初始化，PG 数据表自动创建 |
| Python | 3.14.7 | 全部依赖安装完成（fastapi/uvicorn/pydantic/chromadb/networkx/psycopg/celery/lightrag-hku 等） |

### 3.2 启动方式

```bash
cd /workspace/rca-backend && \
RCA_RUNTIME_MODE=online_full \
OPENCODE_MODEL=opencode/nemotron-3.5-lightning-free \
PG_HOST=localhost PG_PORT=5432 PG_USER=rca PG_PASSWORD=rca PG_DB=rca \
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3.3 健康检查（全组件 UP）

```json
{
  "status": "up",
  "components": {
    "postgres": { "status": "up" },
    "redis":    { "status": "up" },
    "lightrag": { "status": "up" },
    "codegraph":{ "status": "up" },
    "llm":      { "status": "up", "provider": "online_full" }
  },
  "runtime_mode": "online_full",
  "version": "3.0.0"
}
```

### 3.4 LightRAG 1.5.6 适配（本次联调解决的关键项）

- 模块名 `lightrag_hku` → `lightrag`
- `openai_embedding` → `openai_embed`（异步，返回 np.ndarray）
- 构造函数移除 `embedding_dim` 参数，改用 `@wrap_embedding_func_with_attrs(embedding_dim=1024)` 标注嵌入函数
- 存储类型须传类名（`PGKVStorage` / `PGVectorStorage` / `PGGraphStorage`），新增 `_resolve_storage_name()` 做友好名→类名映射

## 4. E2E 测试套件（ASGI 客户端）

```bash
cd /workspace/rca-backend && python -m pytest tests/test_e2e.py -q
```

**结果：7 passed, 57 warnings in 42.67s（全部通过）**

| 用例 | 覆盖内容 | 结果 |
|------|----------|------|
| test_health_up | 健康检查可达，组件状态正确 | ✅ |
| test_frontend_accessible | 前端页面可访问 | ✅ |
| test_analyze_and_stream | 提交分析 → SSE 流式接收 final 事件 | ✅ |
| test_mock_full_flow | 全流程冒烟（提交→状态→结果） | ✅ |
| test_m1_end_to_end_latency | M1 里程碑：端到端延迟 < 30s | ✅ |
| test_top3_output | M1 里程碑：输出 Top3 根因 | ✅ |
| test_state_recovery | 重启后状态恢复 | ✅ |

## 5. 单元回归测试

```bash
cd /workspace/rca-backend && python -m pytest tests/ -k "not e2e and not deploy and not env_validation"
```

**结果：176 passed in 56.00s（全部通过）**

覆盖：引擎编排 / 双闸门（CRAG+HIL）/ 5-Agent / CodeGraph / Code2CN / LightRAG 适配 / 双图谱交叉验证 / 服务级拓扑 / 迁移与增量中文化 / 混合检索等模块。

## 6. online_full 真实联调 HTTP 测试

**结果：25/25 全部通过（耗时约 30s）**

| 类别 | 用例 | 结果 |
|------|------|------|
| 健康检查 | runtime_mode=online_full、5 组件全 UP | ✅ |
| RCA 全流程 | POST /api/v1/rca/analyze → SSE 流 → COMPLETED（Top3 根因 + 方案） | ✅ |
| RAG 检索 | POST /api/v1/rag/query 三种意图路由（history/propagation/architecture） | ✅ |
| RAG 写入 | 文本插入 / 调用图注入（KG） | ✅ |
| Code2CN | POST /api/v1/code2cn/generate（含 LLM 降级兜底 degraded=true） | ✅ |
| CodeGraph | 节点查询 / 404 处理 | ✅ |
| 参数校验 | 非法请求体 → HTTP 400 | ✅ |
| 错误处理 | 不存在资源 → 404 | ✅ |

> 说明：免费模型为 agentic 型（复杂提示词会自动调用 grep/glob/read 等工具），code2cn 生成耗时较长；降级兜底路径（`degraded: true`）验证通过。

## 7. 质量评估（code2cn）

```bash
cd /workspace/rca-backend && python scripts/eval_code2cn.py
```

| 指标 | 结果 | 目标 | 状态 |
|------|------|------|------|
| 符号保留率 | 100.0% | 100% | ✅ |
| 外部调用检出率 | 100.0% | ≥85% | ✅ |
| 异常路径检出率 | 100.0% | ≥80% | ✅ |
| 兜底成功率 | 100.0% | ≥99% | ✅ |
| token 成本比 | 25.0% | ≤30% | ✅ |
| 语义准确性 | 100.0% | ≥90% | ✅ |

**总体达标：PASS**

## 8. 联调中发现并修复的缺陷

| 编号 | 缺陷 | 根因 | 修复 |
|------|------|------|------|
| DEF-01 | opencode 1.18.25 读取 `agent.*.tools` 失败 | 新版要求 `tools` 为对象（`{"codegraph": true}`）而非数组 `["codegraph"]` | 修正 `opencode.json` 中 edit/extract 两个 agent 的 tools 格式 |
| DEF-02 | code2cn 生成走错模型（qwen2.5-coder） | `Code2CN.__init__` 硬编码使用 `config.llm.extract_model` 默认值，未读取 `OPENCODE_MODEL` 环境变量 | 增加 `os.environ.get("OPENCODE_MODEL") or ...` 优先读取环境变量 |

修复后回归：25/25 online_full 测试全部通过。

## 9. 已知限制与风险

| 级别 | 说明 |
|------|------|
| 低 | code2cn 免费模型为 agentic 型，复杂提示词会调用工具导致生成耗时较长（受 180s 超时保护，超时自动降级） |
| 低 | `/api/v1/code2cn/generate` 为 async 端点但内部调用同步 subprocess（opencode CLI），长任务会短暂占用事件循环；建议后续改为后台任务 + 状态轮询 |
| 说明 | 免费模型输出质量上限低于付费模型，方案生成质量建议在生产环境替换为更强的 opencode 模型 |

## 10. 测试结论

| 结论 | 说明 |
|------|------|
| **测试结果** | ✅ **PASS**（7/7 E2E + 176/176 单元 + 25/25 联调 + 6/6 质量指标） |
| 系统可用性 | 服务启动正常、前端可访问、核心 RCA 全流程（提交→SSE→双闸门→方案）在真实组件下打通 |
| 环境联通性 | Postgres / Redis / LightRAG / CodeGraph / opencode LLM 五组件全部 UP，真实数据读写正常 |
| 降级能力 | 三级降级模式正常，LLM/图谱缺失时自动降级并提供可观测信息 |
| 缺陷收敛 | 联调发现 2 个配置/默认值缺陷，均已修复并回归通过 |

---

*报告生成：2026-08-31 · RCA Command v3.0.0 · 测试模式 online_full（完整联调）*

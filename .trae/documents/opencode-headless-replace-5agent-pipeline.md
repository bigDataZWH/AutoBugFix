# 方案：5-Agent 流水线替换为 opencode headless（serve 模式）

> 版本：v1.0 ｜ 日期：2026-09-19 ｜ 状态：待审阅

## 一、摘要（Summary）

将 V3 `RCAEngine.run_sequential` 的 **5-Agent 编排**（A1→A2‖A3→A4→CRAG→HIL→A5，其中 A2/A3/A5 大量使用 `SAMPLE_TICKETS` mock 数据）替换为**单次 opencode serve headless 会话**：由 opencode server 在用户指定的本地代码仓上完成「问题理解→静态+运行时分析→根因定位→CRAG 自评估迭代→方案生成」全流程，Python 层仅保留 **HIL 人工闸门**、SSE 事件代理、RCAState 状态持久化与断点续跑。

V3 API 端点（`/api/v1/rca/*`）与 `RCAState` 模型**完全保留**，前端无感。

## 二、现状分析（Current State Analysis）

### 2.1 双流水线并存

| 维度 | V2 Pipeline `/api/analyze` | V3 RCAEngine `/api/v1/rca/analyze` |
|------|------|------|
| 编排器 | `Pipeline.run_async()` 6 步线性 | `RCAEngine.run_sequential()` 5-Agent |
| opencode 调用 | ✅ `oc.analyze_code()` 真实（per-call `opencode run` 子进程） | ❌ 仅 A1 取票用 opencode；A2/A3/A5 用 `SAMPLE_TICKETS`/`SAMPLE_PRACTICES` mock |
| 闸门 | 无 | CRAG evaluate→rewrite 循环 + HIL 挂起/确认 |
| 输出模型 | `AnalysisReport`（V2） | `RCAState`（V3，含 top3/gate_status/solution） |

### 2.2 V3 5-Agent 的核心问题

1. **A2（静态分析）**：`AgentA2.run()` 返回 `SAMPLE_TICKETS` 派生的 `SuspectFunction` 列表，**不调用 opencode**，非真实代码分析。
2. **A3（运行时分析）**：`AgentA3.run()` 返回硬编码 `AnomalyPath`，**不调用 opencode**。
3. **A5（方案生成）**：`AgentA5.run()` 用 `retriever` + `SAMPLE_PRACTICES`，**不调用 opencode**。
4. **CRAG 闸门**：`crag_gate()` 基于四维 evidence 均值判定，rewrite 循环靠 `_supplement_evidence` 生成 fallback 指标/变更数据（仍是 mock），无法真正补强弱维度。
5. **本质**：5-Agent 拓扑是 Python 手工编排的「伪流水线」，真实智能全缺位，仅 A1 取票与 V2 用到 opencode。

### 2.3 现有 opencode 集成（可复用资产）

- **`opencode.json`**：已配置 `huawei-maas` provider（deepseek-v3 + qwen2.5-coder）、`opencode-codegraph` 插件、`codegraph` MCP（`.codegraph/graph.db`）、`edit`/`extract` 两个 agent。
- **`OpenCodeAdapter`**（`opencode_adapter.py`）：封装 `opencode run` 子进程模式，有 `run_llm`/`analyze_code`/`synthesize_report`/`fetch_yunjie_tickets`。
- **opencode serve API**（web 调研确认）：
  - `POST /session/:sessionID/prompt_async`（异步发 prompt，返回 202）
  - 工作区路由：`?directory=` query 或 `x-opencode-directory` 头指定本地仓路径，每目录独立实例
  - 流式输出 `text/event-stream`，事件含 plan/act/tool 步骤
  - 中间件：Authorization（Basic/`auth_token`）/ WorkspaceRouting / InstanceContext

## 三、决策记录（Assumptions & Decisions）

| # | 决策 | 选项 | 理由 |
|---|------|------|------|
| D1 | 替换范围 | **整条流水线替换为单次 opencode serve 会话** | 用户明确选择；消除 mock 伪流水线，opencode codegraph 工具做真实分析 |
| D2 | 调用方式 | **opencode serve 长驻 server**（非 per-call `opencode run` 子进程） | 用户明确选择；长驻避免子进程启动开销，复用 codegraph 索引，支持流式事件 |
| D3 | API 契约 | **保留 V3 端点 `/api/v1/rca/*` 与 `RCAState`** | 用户明确选择；前端零改动，外部契约稳定 |
| D4 | CRAG 闸门 | **CRAG 自评估逻辑嵌入 opencode prompt** | 用户明确选择；让 LLM 在会话内自行迭代补强弱维度，避免 Python fallback mock |
| D5 | HIL 闸门 | **保留 Python `hil_gate` + `resume`** | 用户明确选择；保留人工干预能力 |
| D6 | 代码仓 | **用户指定本地代码仓路径（`repo_path`），不再 Python clone** | 用户明确选择；适配本地开发/内网场景 |
| D7 | 进度流式 | **代理 opencode serve 事件到现有 SSE 端点** | 用户明确选择；前端实时看 plan/act/tool 步骤 |
| D8 | 会话数 | **主流程单会话**（分析+CRAG自评+方案一次产出），HIL 为 Python 后置闸门；`modify` 动作可选触发轻量补丁会话 | 兑现「单次会话」要求，同时保留 HIL 语义 |

## 四、新老方案优缺点对比

### 4.1 架构对比

```
【老方案：5-Agent Python 编排】
A1(取票/症状) → A2‖A3(mock静态‖mock运行时) → A4(cross_validate评分)
  → CRAG(crag_gate均值判定 + _supplement_evidence fallback mock 重写)
  → HIL(挂起/确认) → A5(retriever+SAMPLE_PRACTICES 方案)
  ↑ 6 个 Python 阶段，4 个 Agent 类，真实智能仅在 A1 取票

【新方案：opencode serve 单会话】
用户指定 repo_path → opencode serve 会话(codegraph 工具真实分析 + CRAG 自评迭代 + 方案生成)
  → 输出 JSON{top3,crag_verdict,solution} → 映射 RCAState
  → HIL(Python 后置闸门) → [可选] modify 触发轻量补丁会话
  ↑ 1 次 opencode 会话替代 5-Agent，codegraph 工具做真分析
```

### 4.2 优缺点矩阵

| 维度 | 老方案（5-Agent） | 新方案（opencode serve） |
|------|------|------|
| **分析真实性** | ❌ A2/A3/A5 用 mock；仅 A1 取票真实 | ✅ codegraph 工具真实分析调用结构/数据流/热点 |
| **CRAG 质量** | ⚠️ crag_gate 均值判定 + fallback mock 补强，无法真正补弱维度 | ✅ LLM 会话内自评+自迭代，可调用工具补证据 |
| **编排复杂度** | ❌ 6 阶段 + ThreadPoolExecutor fan-out + 多辅助方法（~400 行 engine） | ✅ 1 会话 + 输出映射（编排大幅简化） |
| **延迟** | ⚠️ 多阶段串行，每阶段 mock 即时但无真分析 | ⚠️ 单次会话需真实 LLM+工具调用，首次较慢；长驻 serve 摊薄启动成本 |
| **可观测性** | ✅ 6 阶段 SSE 事件，粒度清晰 | ✅ opencode plan/act/tool 事件流代理到 SSE，更细粒度 |
| **HIL 干预** | ✅ top3 产出后挂起，modify 可改 top3 再走 A5 | ⚠️ 主会话已含方案；modify 需触发补丁会话（额外复杂度） |
| **降级容错** | ✅ mock_demo 模式无 opencode 也能跑 | ⚠️ serve 不可用需降级回 mock（保留 fallback 路径） |
| **状态/断点续跑** | ✅ StateStore 按 stage.index 续跑 | ⚠️ 会话级断点续跑需 opencode session 持久化支持，降级为整会话重跑 |
| **成本** | ⚠️ mock 阶段零 token，但无真实价值 | ⚠️ 每次真实 LLM+工具调用消耗 token，需控制 prompt 长度 |
| **可测试性** | ✅ 各 Agent 可独立单测 | ⚠️ 需 mock OpenCodeServeAdapter；集成测试依赖 serve 可用 |
| **前端兼容** | ✅ V3 端点+RCAState 不变 | ✅ 完全不变 |

### 4.3 关键风险与缓解

| 风险 | 缓解 |
|------|------|
| opencode serve 未部署时全链路不可用 | 保留 `mock_demo` 降级：serve 不可用 → 回退 mock RCAState（degraded=True），保证 CI/演示可用 |
| opencode 输出 JSON 解析失败 | 复用 `_extract_json` 正则兜底 + 失败回退 mock top3 |
| HIL modify 后方案与 top3 不一致 | modify 默认仅记录人工 top3 调整 + 触发轻量补丁会话重生成 patch |
| serve 会话超时/卡死 | adapter 设 timeout，超时回退 mock 并标记 degraded |
| codegraph 索引未构建 | prompt 内引导 opencode 先建索引；serve 启动时预热 |

## 五、改动清单（Proposed Changes）

### 5.1 新增文件

#### `app/opencode_serve_adapter.py`（新增）
opencode serve HTTP 适配器，封装会话生命周期与事件流消费。

```python
class OpenCodeServeAdapter:
    def __init__(self, base_url: str, auth_token: str = "", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self.available = self._health()

    def _health(self) -> bool:
        # GET {base_url}/health，返回 bool

    def create_session(self, directory: str) -> str:
        # POST {base_url}/session（?directory=<directory>），返回 session_id

    def prompt_async(
        self, session_id: str, prompt: str,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        # POST /session/{session_id}/prompt_async
        # 轮询/流式消费事件 → on_event 回调 → 返回最终 JSON 结果

    def close_session(self, session_id: str) -> None:
        # DELETE /session/{session_id}

    @staticmethod
    def _extract_json(text: str) -> dict:
        # 复用 opencode_adapter._extract_json 正则
```

**为什么**：serve 模式是 HTTP 长连接，与现有 `opencode run` 子进程模式（`OpenCodeAdapter`）协议不同，独立适配器职责清晰；保留 `OpenCodeAdapter` 供 V2 Pipeline 与 yunjie 取票继续使用。

#### `app/rca_prompt.py`（新增）
集中管理 RCA 会话 prompt 模板（含 CRAG 自评估指令），便于迭代与单测。

```python
def build_rca_prompt(bug_info: dict, repo_path: str, max_rewrite_rounds: int = 3) -> str:
    """构建 RCA 会话 prompt：问题理解→codegraph 分析→Top-3 定位→CRAG 自评迭代→方案生成。"""

def build_patch_prompt(confirmed_top3: list[dict], bug_info: dict) -> str:
    """HIL modify 后的轻量补丁会话 prompt：基于确认 top3 重生成 patch/steps。"""
```

### 5.2 修改文件

#### `app/engine.py`（重构 `run_sequential` 与 `resume`）
**保留**：`SSEEventBus`、`StateStore`、`generate_task_id`、`resume`/`resume_from_checkpoint`/`get_state`、`_fail`、`_candidates_to_rootcauses`/`_rootcause_to_candidate`（HIL 回灌用）、`_publish_stage`。

**替换** `run_sequential` 内部实现：

```python
def run_sequential(self, state: RCAState) -> RCAState:
    try:
        self._ensure_task_id(state)
        self.store.save(state)

        # Stage 1: opencode serve 单会话（分析+CRAG自评+方案）
        if state.stage.index < 4:
            state = self._apply_opencode_session(state)

        # Stage 2: HIL 后置闸门（Python）
        if state.stage.index < 5 or state.gate_status.hil == "pending":
            state = self._apply_hil(state)
            if state.gate_status.hil == "pending":
                self.store.save(state)
                return state

        state.stage = Stage(index=6, name="COMPLETED", status="completed")
        # gate_status 收口 + flywheel writeback（保留现有逻辑）
        ...
        return state
    except RCAError as exc:
        return self._fail(state, exc.code, exc.message)
    except Exception as exc:
        return self._fail(state, "UNKNOWN", str(exc))
```

新增方法：
- `_apply_opencode_session(state)`：
  1. 构建 prompt（`build_rca_prompt`，含 CRAG 自评指令）
  2. `serve.create_session(repo_path)` → `serve.prompt_async(sid, prompt, on_event=self._proxy_event)`
  3. `_proxy_event` 把 opencode 事件转成 SSEEventBus 事件（stage_start/complete）
  4. 解析输出 JSON → 映射 `state.symptoms/error_type/suspect_services/top3/gate_status.crag/solution`
  5. serve 不可用 → 回退 mock（`degraded=True`，复用现有 `_candidates_to_rootcauses` + `SAMPLE_TICKETS`）
- `_apply_hil(state)`：复用 `hil_gate`，confidence < threshold → pending + publish gate_pending
- `_map_opencode_output(raw: dict, state: RCAState)`：JSON→RCAState 字段映射

**`resume` 调整**：HIL confirm → 直接收口；modify → 触发 `serve.prompt_async(build_patch_prompt)` 轻量补丁会话重生成 `solution`。

**移除**：`_apply_a1`/`_apply_a2a3_parallel`/`_apply_a4`/`_apply_gates`/`_apply_a5`/`_collect_supplementary_data`/`_supplement_evidence`/`_generate_fallback_*`（5-Agent 专属）。`__init__` 不再持有 `a1/a2/a3/a5`。

**为什么**：用户要求整条流水线替换；保留 HIL/StateStore/SSE 价值组件，剔除 mock 伪编排。

#### `app/models.py`（新增字段）
`AnalyzeRequest` 新增 `repo_path: str = ""`（用户指定本地代码仓绝对路径）。
`RCAState` 无需改字段（`bug_info.link`/现有字段承载 repo_path，或复用 `bug_info.environment`）。

**为什么**：D6 决策，用户指定本地仓，不再 Python clone。

#### `app/main.py`（注入 adapter + 传递 repo_path）
- 新增 `serve_adapter = OpenCodeServeAdapter(base_url=config.opencode_serve.base_url, ...)`
- `engine.set_serve_adapter(serve_adapter)`（engine 新增 `self.serve` 持有）
- `v3_analyze`：把 `req.repo_path` 写入 `state.bug_info.environment["repo_path"]`，传给 engine
- **移除** `engine.a1 = AgentA1(...)` / `engine.a5 = AgentA5(...)` 注入（A1/A5 不再被 engine 使用）
- **保留** V2 `opencode`/`pipeline`/yunjie 取票路径不变

#### `app/config.py`（新增配置）
```python
@dataclass
class OpenCodeServeConfig:
    base_url: str = os.environ.get("OPENCODE_SERVE_URL", "http://localhost:4096")
    auth_token: str = os.environ.get("OPENCODE_SERVE_TOKEN", "")
    timeout: int = int(os.environ.get("OPENCODE_SERVE_TIMEOUT", "300"))

@dataclass
class AppConfig:
    ...
    opencode_serve: OpenCodeServeConfig = field(default_factory=OpenCodeServeConfig)
```

#### `tests/test_engine.py`（更新受影响用例）
- **UT1/UT6/UT7/UT8/UT11**：不变（RCAState/SSE/StateStore/降级/fixture）
- **UT2 `TestSequentialOrchestrator`**：mock `OpenCodeServeAdapter`，断言 `run_sequential` 仍返回 COMPLETED 且 top3/solution 非空
- **UT4 `TestRootCauseAnalysis`**：改为断言 opencode 输出映射后的 top3
- **UT5 `TestAgentA5`**：改为 `TestSolutionGeneration`，断言 solution 来自会话输出
- **UT9 `TestBreakpointResume`**：保留，验证断点续跑
- **UT10 `TestHILResume`**：保留，验证 HIL confirm/modify/reject
- 新增 `TestOpenCodeServeAdapter`：mock HTTP，验证 create_session/prompt_async/事件代理/JSON 解析/降级回退

#### `opencode.json`（可选，新增 RCA agent 配置）
```json
"agent": {
  "rca": { "model": "deepseek-v3", "tools": { "codegraph": true } }
}
```

### 5.3 保留不动文件
- `app/opencode_adapter.py`：V2 Pipeline + yunjie 取票仍用 `opencode run` 模式
- `app/agents.py`：`AgentA1` 被 `tests/test_engine.py UT3` 直接单测；A2/A3/A5 标记 deprecated（保留以降风险，后续可清）
- `app/pipeline.py`：V2 路径完全不动
- `app/dual_graph.py`/`gates.py`/`flywheel.py`：HIL/评分/飞轮逻辑保留

## 六、数据流（Data Flow）

```
POST /api/v1/rca/analyze {bug_link, repo_path, runtime_mode}
  │
  ▼
engine.run_sequential(state)
  │
  ├─ Stage1: _apply_opencode_session
  │    ├─ serve.create_session(repo_path) → sid
  │    ├─ serve.prompt_async(sid, build_rca_prompt(bug, repo))
  │    │     └─ opencode 会话内：理解→codegraph 分析→Top3→CRAG自评迭代→方案
  │    ├─ on_event → events.publish(task_id, "stage_*", opencode_event)  ← SSE 代理
  │    └─ _map_opencode_output(raw) → state{top3, crag_verdict, solution, ...}
  │
  ├─ Stage2: _apply_hil
  │    ├─ hil_gate(top3, confidence)
  │    └─ confidence < threshold → gate_status.hil=pending → publish gate_pending → return
  │
  └─ COMPLETED → flywheel.writeback → events.publish final
                                       │
GET /api/v1/rca/{id}/stream  ← 前端订阅 opencode 代理事件
POST /api/v1/rca/{id}/confirm {action: modify, modified_top3}
  │
  ▼
engine.resume → serve.prompt_async(build_patch_prompt) → solution 重生成 → final
```

## 七、降级与边界（Edge Cases / Failure Modes）

| 场景 | 行为 |
|------|------|
| opencode serve 不可用（`_health=False`） | `degraded=True`，回退 mock top3/solution（复用 `SAMPLE_TICKETS` + `_candidates_to_rootcauses`），保证 CI/演示 |
| `repo_path` 为空或路径不存在 | 校验返回 400，或降级 mock |
| opencode 输出非 JSON / 字段缺失 | `_extract_json` 正则兜底 + 字段默认值 + degraded |
| 会话超时 | adapter timeout → `_fail(state, "SERVE_TIMEOUT")` |
| HIL pending 时 serve 重启 | resume 时重建会话，重跑分析（降级为整会话重跑） |
| `runtime_mode=mock_demo` | 跳过 serve 调用，直接 mock（保证现有 198 测试不破） |

## 八、验证步骤（Verification）

1. **单测**：`cd rca-backend && python -m pytest tests/test_engine.py tests/test_opencode_serve_adapter.py -v`
   - mock_demo 模式下所有用例通过（不依赖真实 serve）
   - 新增 adapter 单测覆盖 create/prompt/事件/降级
2. **全量回归**：`python -m pytest -q`，目标 198+ 用例 0 失败
3. **集成测试**（serve 可用时）：`OPENCODE_SERVE_URL=http://localhost:4096` 启动真实 serve，验证 `/api/v1/rca/analyze` 端到端产出真实 top3/solution
4. **降级测试**：不启动 serve，验证 degraded 路径仍返回完整 RCAState
5. **HIL 测试**：构造低 confidence 场景，验证 pending→confirm/modify/reject 全链路
6. **SSE 验证**：`curl -N /api/v1/rca/{id}/stream`，确认 opencode 事件被代理
7. **lint/typecheck**：`ruff check app/ && python -m pyright app/`（若项目配置）

## 九、实施顺序（Rollout）

1. 新增 `OpenCodeServeConfig`（config.py）+ `OpenCodeServeAdapter`（新文件）+ `rca_prompt.py`
2. 重构 `engine.py`：新增 `_apply_opencode_session`/`_apply_hil`/`_map_opencode_output`，保留 mock 降级分支
3. 改 `models.py`（repo_path）+ `main.py`（注入 adapter/传参/移除旧注入）
4. 更新 `tests/test_engine.py` + 新增 `tests/test_opencode_serve_adapter.py`
5. mock_demo 全量回归 → 真实 serve 集成验证 → HIL/SSE 验证
6. 清理：移除 `agents.py` 中 A2/A3/A5（可选，标 deprecated 降风险）

---

**待用户确认后即可进入执行阶段。**

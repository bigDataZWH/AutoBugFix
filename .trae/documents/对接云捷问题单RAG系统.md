# 对接云捷问题单 RAG 系统实施计划

## 1. 概述（Summary）

通过 **opencode CLI** 驱动其工具能力，从华为云云捷系统获取**问题单及其关联 MR**，整理为符合 RCA 知识库输入的 `KBImportItem` 格式（一个问题单聚合其所有关联 MR），**双写** ChromaDB（向量+BM25 混合检索）与 LightRAG（pgvector+BGE-M3），并接入三个消费环节：**A1 问题单拉取**、**A5 历史修复检索**、**飞轮自动回写**。

数据流向：`云捷问题单/MR → opencode CLI 采集整理 → KBImportItem → ChromaDB + LightRAG 双写 → A1/A5/飞轮消费`

触发方式（三者并存）：
1. **手工批量导入端点** `POST /api/v1/yunjie/import`（传入问题单 ID/链接列表）
2. **A1 按链接自动拉取**（分析流程中传入 `bug_link` 时自动从云捷获取详情）
3. **飞轮自动回写**（RCA 任务确认后自动把根因+修复写回知识库）

## 2. 现状分析（Current State Analysis）

基于对实际代码的探查，当前状态如下：

| 组件 | 文件 | 现状 | 缺口 |
|---|---|---|---|
| OpenCodeAdapter | `rca-backend/app/opencode_adapter.py` | 通过 `subprocess.run(["opencode","run","--model",model,prompt])` 调 CLI；有 `run_llm`/`analyze_code`/`synthesize_report`/`_extract_json`；不可用时返回 mock | 无云捷数据采集方法 |
| 知识库写入 | `main.py` | `/api/kb/import`→`retriever.import_tickets`（仅 ChromaDB）；`/api/v1/rag/insert`→`lightrag.ainsert`（仅 LightRAG） | 无双写端点、无云捷端点 |
| ChromaDB 检索 | `retriever.py` | `import_tickets(items:list[KBImportItem])` upsert；`hybrid_search(query,microservice,top_k)` 0.6*向量+0.4*BM25 融合返回 `list[KBMatch]` | 已就绪，仅未被 A5 调用 |
| AgentA1 | `agents.py#_fetch_ticket`(L48-52) | 对 `SAMPLE_TICKETS` 做 `t["ticket_id"] in link` 子串匹配 | 未走 opencode 拉取云捷 |
| AgentA5 | `agents.py#run`(L213-238) | `t["module"]==rc.located_function or t["error_code"]==error_type` 精确匹配 `SAMPLE_TICKETS` | 未走向量检索；构造函数无 retriever 注入 |
| 飞轮 | `flywheel.py` | `Flywheel.writeback(payload)`→`lightrag.ainsert`+`SIMILAR_TO` 边；`extract_payload(...)`；`flywheel` 单例；已含 `_inserted_digests` 去重 | 仅 async `writeback`，无 sync 包装；未接入 engine 主流程 |
| Engine | `engine.py` | `run_sequential` 完成后置 `stage=COMPLETED` 并推 `final` 事件；`resume` 在 confirm/modify 后重跑 `run_sequential`；`engine` 模块级单例，`__init__` 内 `self.a1=AgentA1()` 等（用 config 默认 opencode） | 完成后未触发飞轮回写；agent 未注入 main.py 的 retriever/opencode |
| 配置 | `config.py` | `AppConfig.opencode_binary`/`opencode_model` 环境变量；`runtime_mode` 默认 `mock_demo` | 无云捷相关配置项 |

关键约束：
- **纯 opencode CLI 驱动**，不引入 `httpx` 等新依赖，依赖 opencode 自身工具能力访问云捷。
- opencode 不可用时各环节须**优雅降级**（返回空/回退 SAMPLE_TICKETS），绝不污染知识库。

## 3. 设计决策（Assumptions & Decisions）

来自 Phase 2/3 用户确认：

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| D1 | 获取路径 | 纯 opencode CLI 驱动 | 依赖 opencode 工具能力，零新依赖 |
| D2 | MR 映射 | 问题单聚合关联 MR（一条 KBImportItem） | `fix_code`←所有关联 MR diff 拼接；`root_cause`←MR/commit 描述 |
| D3 | 触发端点 | 批量导入 + A1 按链接拉取 + 飞轮自动回写（三选全选） | 覆盖人工/自动双通道 |
| D4 | 写入目标 | ChromaDB + LightRAG 双写 | 兼顾混合检索与图增强语义检索 |
| D5 | 对接方向 | 仅回写（云捷→RCA 知识库） | 不向云捷回写 |
| D6 | 消费优先级 | A1 拉取、A5 检索、飞轮回写 | 三者构成闭环 |
| D7 | 月维度输入 | 手工导入或输入问题单链接 | 通过端点/A1 链接传入 |

派生假设：
- opencode 已配置云捷访问能力（在执行环境中 `opencode` 二进制可用且具备相应工具/权限）；本计划不负责配置 opencode 本身。
- `KBImportItem` 模型字段足以承载云捷数据（无需新增必填字段）；若需 `month` 维度，可复用 `severity` 或在 metadata 中扩展，但**本计划不新增模型字段**以保持比例适当。

## 4. 实施变更（Proposed Changes）

### 4.1 `rca-backend/app/opencode_adapter.py` — 新增云捷采集方法

**What**: 新增 `fetch_yunjie_tickets(ticket_refs: list[str]) -> list[dict]` 方法。

**Why**: 这是"纯 opencode CLI 驱动"获取云捷数据的唯一入口，供批量导入端点与 A1 复用。

**How**:
- 复用现有 `run_llm(prompt)` 与 `_extract_json(text)`（当前 `_extract_json` 用 `re.search(r"\{[\s\S]*\}", text)` 匹配单个 JSON 对象）。
- 对每个 `ref`（问题单 ID 或链接）单独发一次 opencode 调用，prompt 要求 opencode 用其工具能力从云捷获取该问题单及**关联的所有 MR**，聚合为**一个** JSON 对象（字段对齐 `KBImportItem`：`ticket_id/title/description/root_cause/fix_code/microservice/module/error_code/severity`）。
  - `root_cause` ← MR/commit 描述；`fix_code` ← 所有关联 MR 的 diff 拼接。
- `self.available` 为 False 或解析无 `ticket_id` 时跳过该 ref。
- 返回 `list[dict]`（每个元素可直接 `KBImportItem(**d)`）。
- prompt 模板（单条）：

```text
你是一个云捷系统数据采集助手。请使用你的工具能力从华为云云捷系统获取问题单：{ref}
并聚合其关联的所有 MR（Merge Request）。整理为严格 JSON 对象，字段：
ticket_id, title, description, root_cause, fix_code, microservice, module, error_code, severity。
约定：root_cause 取自 MR/commit 描述，fix_code 取自所有关联 MR 的 diff 拼接，
一个问题单输出一个对象。不要输出 JSON 以外的文字。
```

- **不修改** `_extract_json` 签名（保持向后兼容）；如返回为列表则在方法内兼容 `isinstance` 处理。

### 4.2 `rca-backend/app/main.py` — 新增双写端点 + agent 重绑定

**What**:
1. 新增 `POST /api/v1/yunjie/import` 端点。
2. 在 `retriever`/`opencode`/`engine` 初始化后，重绑定 engine 单例的 A1/A5（注入共享的 opencode/retriever）。

**Why**:
- 双写端点统一 ChromaDB + LightRAG 写入，避免调用方分别请求两个端点。
- engine 模块级单例在 `engine.py` 内用 config 默认值构造 agent，未持有 main.py 的 `retriever`；在 main.py（组合根）重绑定即可让 A5 用到向量检索，无需改动单例创建逻辑。

**How**:

端点（新增于 V3 端点区块）：
```python
class YunjieImportRequest(BaseModel):
    ticket_refs: list[str]

@app.post("/api/v1/yunjie/import")
async def yunjie_import(req: YunjieImportRequest):
    raw = opencode.fetch_yunjie_tickets(req.ticket_refs)
    items = [KBImportItem(**d) for d in raw if d.get("ticket_id")]
    n_chroma = retriever.import_tickets(items)
    n_rag = 0
    for it in items:
        text = f"问题单 {it.ticket_id}：{it.title}\n描述：{it.description}\n根因：{it.root_cause}\n修复：{it.fix_code}"
        if await lightrag.ainsert(text, ids=f"yunjie:{it.ticket_id}"):
            n_rag += 1
    return {
        "fetched": len(raw), "imported_chroma": n_chroma,
        "imported_lightrag": n_rag, "degraded": not lightrag.available,
        "kb_total": retriever.count(),
    }
```
（`YunjieImportRequest` 定义置于该端点上方或 `models.py`；为最小改动就近定义在 main.py 内，与现有 `KBImportRequest` 风格一致——`models.py` 中已有 `KBImportItem`，直接 import 即可。）

agent 重绑定（置于 `pipeline = Pipeline(...)` 之后）：
```python
from .agents import AgentA1, AgentA5
engine.a1 = AgentA1(opencode=opencode)
engine.a5 = AgentA5(retriever=retriever, opencode=opencode)
```

### 4.3 `rca-backend/app/agents.py` — 改造 AgentA1._fetch_ticket

**What**: `_fetch_ticket(link)` 改为优先走 opencode 拉取云捷，失败回退 `SAMPLE_TICKETS`。

**Why**: A1 当前仅做本地 mock 子串匹配，无法获取真实云捷问题单详情。

**How**:
```python
def _fetch_ticket(self, link: str) -> Optional[dict[str, Any]]:
    if link and self.opencode.available:
        raw = self.opencode.fetch_yunjie_tickets([link])
        if raw and raw[0].get("ticket_id"):
            return raw[0]
    for t in SAMPLE_TICKETS:
        if link and t["ticket_id"] in link:
            return t
    return None
```
- `AgentA1.__init__` 已持有 `self.opencode`，无需改签名。
- opencode 不可用或拉取为空时回退 `SAMPLE_TICKETS`，保持 `mock_demo` 兼容与现有测试稳定。

### 4.4 `rca-backend/app/agents.py` — 改造 AgentA5.run（注入 retriever）

**What**:
1. `AgentA5.__init__` 新增 `retriever: Optional["Retriever"] = None`（用 `TYPE_CHECKING` 前置声明避免循环导入）。
2. `run` 中优先 `retriever.hybrid_search(...)` 取历史修复，回退 `SAMPLE_TICKETS`。

**Why**: A5 当前用 `module/error_code` 精确匹配 mock 数据，无法做语义检索；改造后走 ChromaDB 混合检索。

**How**:
```python
class AgentA5:
    def __init__(self, opencode=None, retriever=None) -> None:
        self.opencode = opencode or OpenCodeAdapter(binary=config.opencode_binary, model=config.llm.query_model)
        self.retriever = retriever

    def run(self, top3, error_type="") -> Solution:
        historical, best_practices = [], []
        # 优先向量检索历史修复
        if self.retriever is not None:
            for rc in top3:
                q = f"{rc.root_cause} {rc.located_function} {error_type}"
                ms = [c.microservice for c in top3 if c.located_function] or None
                matches = self.retriever.hybrid_search(q, microservice=ms[0] if ms else None, top_k=5)
                for m in matches:
                    if m.fix_code and m.fix_code not in historical:
                        historical.append(m.fix_code)
                    if m.root_cause and m.root_cause not in best_practices:
                        best_practices.append(m.root_cause)
        # 回退 / 补充：SAMPLE_TICKETS 精确匹配
        if not historical:
            for rc in top3:
                for t in SAMPLE_TICKETS:
                    if t["module"] == rc.located_function or t["error_code"] == error_type:
                        if t["fix_code"] and t["fix_code"] not in historical:
                            historical.append(t["fix_code"])
                        if t["root_cause"] and t["root_cause"] not in best_practices:
                            best_practices.append(t["root_cause"])
        # 保留现有 SAMPLE_PRACTICES 兜底（best_practices）
        ...
        return Solution(...)
```
- 保留 `_compose_patch` 与 `test_cases` 逻辑不变。
- 检索为空时回退 SAMPLE_TICKETS，确保 `mock_demo`/空库场景不退化。

### 4.5 `rca-backend/app/engine.py` — 飞轮自动回写接入

**What**: 在 `run_sequential` 完成路径（`stage=COMPLETED` 之后、`return state` 之前）调用 `flywheel.writeback`；新增 `Flywheel.writeback_sync` 同步包装。

**Why**: 飞轮目前未自动接入引擎，确认后的根因+修复无法沉淀为知识。

**How**:

`flywheel.py` 新增同步包装（参照 `lightrag_adapter.route_query` 用 `asyncio.run` 的既有模式）：
```python
def writeback_sync(self, payload: FlywheelPayload) -> WritebackResult:
    import asyncio
    try:
        return asyncio.run(self.writeback(payload))
    except Exception:
        return WritebackResult(inserted=0, similar_edges=[])
```

`engine.py`：
- 顶部 import：`from .flywheel import flywheel`（engine 已 import `lightrag`，追加 flywheel）。
- `run_sequential` 末尾，在 `self.store.save(state)` + `final` 事件发布之后、`return state` 之前插入 best-effort 回写：
```python
# 仅在确认/完成且非驳回时回写（rejected 在 resume 中提前 return，不会走到这里）
try:
    payload = flywheel.extract_payload(
        root_cause=state.top3[0].root_cause if state.top3 else "",
        root_cause_function=state.top3[0].located_function if state.top3 else "",
        call_path=state.P_runtime.functions,
        fix_patch=state.solution.patch_suggestion,
        verify_case="; ".join(state.solution.test_cases),
        ticket_id=state.bug_info.bug_id,
        title=state.bug_info.title,
        description=state.bug_info.description,
    )
    flywheel.writeback_sync(payload)
except Exception:
    pass
```
- 回写位于 `final` 事件发布**之后**，保证用户先拿到结果；best-effort 包裹异常，绝不影响主流程。
- `resume` 的 reject 分支提前 `return`（既有逻辑），不触发回写；confirm/modify 分支调 `run_sequential` → 自动回写一次（`_inserted_digests` 去重防重复）。

## 5. 数据流（Data Flow）

```
[手工导入] POST /api/v1/yunjie/import {ticket_refs}
   └─ opencode.fetch_yunjie_tickets(refs)  -- opencode CLI 采集云捷问题单+MR
        └─ list[dict] (KBImportItem 字段，MR 聚合)
             ├─ retriever.import_tickets(items) ─→ ChromaDB (向量+BM25)
             └─ lightrag.ainsert(text, ids=yunjie:{ticket_id}) ─→ LightRAG

[A1 自动拉取] engine.run_sequential → AgentA1._fetch_ticket(bug_link)
   └─ opencode.fetch_yunjie_tickets([link])[0] ─→ BugInfo 合并（失败回退 SAMPLE_TICKETS）

[A5 检索] engine → AgentA5.run(top3)
   └─ retriever.hybrid_search(query, microservice, top_k=5) ─→ KBMatch[]
        └─ historical_cases←fix_code, best_practices←root_cause（空则回退 SAMPLE_TICKETS）

[飞轮回写] run_sequential 完成 → flywheel.writeback_sync(payload)
   └─ lightrag.ainsert + SIMILAR_TO 边（去重）
```

## 6. 验证步骤（Verification）

1. **单元/回归**：`cd rca-backend && python -m pytest -q`，确认现有 `test_gates_flywheel.py` 25 项仍通过；新增/改造不引入回归（已知 9 项 test_engine 失败为既有 mock_demo 置信度问题，非本次引入）。
2. **opencode 不可用降级**：未安装 opencode 时，`fetch_yunjie_tickets` 返回 `[]`；`A1._fetch_ticket` 回退 `SAMPLE_TICKETS`；`A5.run` 回退 `SAMPLE_TICKETS`；端点返回 `imported_chroma=0`。
3. **双写端点**（opencode 可用时）：`POST /api/v1/yunjie/import {"ticket_refs":["<某问题单ID>"]}` → 校验 `imported_chroma>=1` 且 `/api/kb/count` 增长；`GET` 或查询验证 LightRAG 命中 `yunjie:{ticket_id}`。
4. **A5 检索**：导入后发起 `POST /api/v1/rca/analyze`（传入相似 bug 链接），确认 A5 `historical_cases` 命中导入的 `fix_code`（非仅 SAMPLE_TICKETS）。
5. **飞轮回写**：完成一次确认的分析任务后，检查 `flywheel._inserted_digests` 非空或 LightRAG 出现新 `ticket:` 文本；驳回任务不写回。
6. **类型/启动检查**：`python -c "from app.main import app"` 无导入错误；`GET /api/v1/health` 仍 `up`。

## 7. 兼容性与回退（Compatibility & Fallback）

- **mock_demo 模式**：opencode 不可用时所有新逻辑回退至 `SAMPLE_TICKETS`/空，行为与现状一致。
- **双写部分失败**：ChromaDB 写入成功但 LightRAG degraded 时，端点返回 `degraded:true`，不阻断 ChromaDB 写入。
- **飞轮幂等**：`_inserted_digests` 去重 + best-effort 异常吞噬，回写失败不影响 RCA 主流程与已返回结果。
- **无模型/无字段改动**：复用 `KBImportItem`/`FlywheelPayload`/`KBMatch`，零迁移成本。
- **无新依赖**：纯 opencode CLI + 现有 chromadb/lightrag/rank_bm25。

## 8. 不在本次范围（Out of Scope）

- 前端 `rca-command.html` 的导入 UI（可后续追加，后端端点已可直接用 curl/接口工具触发）。
- opencode 本身对云捷的访问能力配置（假设执行环境已具备）。
- 向云捷回写（用户明确仅回写方向为云捷→RCA）。
- 按月自动调度/分片（用户明确为手工导入或链接输入；如需定时可后续用 Schedule 工具单独建立）。

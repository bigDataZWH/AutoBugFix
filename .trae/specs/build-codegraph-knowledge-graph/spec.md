# CodeGraph 代码图谱构建 Spec

## Why
V2.0 需自行实现 AST 解析、跨文件符号解析、调用边提取，造轮子成本高且跨语言支持弱。V3.0 改用 opencode 官方支持的 `opencode-codegraph@0.1.38` 插件 + CodeGraph（tree-sitter 解析 AST→提取符号与调用/继承/引用边→存 SQLite 知识图谱→经 MCP Server 暴露给 AI 工具）替代手写 tree-sitter 调用图构建，100% 本地运行、Windows 有 install.ps1，集成成本最低且经 7 个真实开源仓库实测平均节省 35% 成本/59% token/49% 耗时/70% 工具调用。

## What Changes
- **BREAKING**：弃用 V2.0 手写 tree-sitter 调用图构建模块，改用 opencode-codegraph 插件自动构建 Code Property Graph（CPG），删除自研 AST/边提取代码路径。
- 新增 opencode-codegraph 插件注册：`opencode.json` 中 `plugin: ["opencode-codegraph"]` 与 `mcp.codegraph`（command/args/env.CODEGRAPH_DB），npm 包版本锁定 `opencode-codegraph@0.1.38`。
- 新增 CodeGraph 四层架构落地：tree-sitter 解析 → 符号·边提取 → SQLite 图谱存储 → MCP Server。
- 新增克隆代码仓后六步自动构建流程：STEP1 克隆 repo@branch → STEP2 `codegraph init -i` → STEP3 `codegraph index`（tree-sitter 全仓→符号/边→SQLite）→ STEP4 语义中文化 → STEP5 `insert_custom_kg` 注入 LightRAG → STEP6 增量更新（git commit 后重建受影响子树）。
- 新增 MCP 工具查询接口：`codegraph_callers(symbol, depth)` / `codegraph_callees(symbol)` / `codegraph_explore(symbol)` / `codegraph_taint(entry, sink)`，返回字段含 `fan_in`/`fan_out`/圈复杂度/方法数/安全发现。
- 新增 CPG 数据结构：节点 `{symbol, type, file, line, fan_in, fan_out, complexity, cn_summary}`，边 `{src, tgt, type, weight}`，存储于本地 `.codegraph/graph.db`（FTS5 全文搜索 + 关系图）。
- 新增增量构建能力：git commit 后按文件变更集仅重建受影响子树，避免全量重建。
- 新增跨语言调用链断裂兜底：桥接节点 + 人工标注回流机制。

## Impact
- Affected specs: generate-code-chinese-outline（函数节点 `cn_summary` 字段由本模块产出供其消费）、setup-lightrag-retrieval-engine（`insert_custom_kg` 注入依赖本模块 CPG 节点输出）、implement-dual-graph-validation（函数级调用图数据源来自本模块 SQLite）、orchestrate-five-agent-engine（A2 代码分析使用本模块 MCP 工具构建静态嫌疑函数集 S_static）。
- Affected code: CodeGraph 构建编排模块（克隆后六步流水线）、opencode.json 插件配置、MCP 工具适配层（callers/callees/explore/taint 封装）、增量构建调度器、`.codegraph/graph.db` 存储层、V2.0 手写 tree-sitter 模块（待删除）。

## ADDED Requirements

### Requirement: opencode-codegraph 插件集成
The system SHALL 通过 opencode.json 注册 opencode-codegraph 插件与 codegraph MCP Server，配置 `command: "codegraph"`、`args: ["mcp"]`、`env.CODEGRAPH_DB` 指向本地 `.codegraph/graph.db`，使对话中提及文件即自动注入 CPG 上下文（方法数、圈复杂度、fan_in/fan_out、安全发现、调用关系），并支持污点分析。

#### Scenario: 插件注册与 MCP Server 就绪
- **WHEN** opencode 启动并加载 opencode.json
- **THEN** codegraph MCP Server 就绪，提及文件时自动注入 CPG 上下文，npm 包版本为 opencode-codegraph@0.1.38

#### Scenario: MCP 上下文自动注入
- **WHEN** 用户在对话中提及某源文件路径
- **THEN** 自动注入该文件涉及的 CPG 上下文（方法数/圈复杂度/fan_in·fan_out/安全发现/调用关系），无需手动调用工具

#### Scenario: DB 路径不存在兜底
- **WHEN** `env.CODEGRAPH_DB` 指定的 .codegraph/graph.db 尚未初始化
- **THEN** MCP Server 启动时给出明确提示「请先执行 codegraph init -i 与 codegraph index」，而非静默失败或崩溃

#### Scenario: 插件版本不匹配
- **WHEN** 安装的 opencode-codegraph 版本低于 0.1.38 或与 codegraph CLI 主版本不兼容
- **THEN** 启动时输出版本冲突警告并给出升级指令，阻止以降级能力运行

### Requirement: 克隆代码仓后自动构建流程
The system SHALL 在 opencode 拉取 repo@branch 后自动执行六步构建：STEP1 克隆代码仓 → STEP2 `codegraph init -i` 初始化 .codegraph → STEP3 `codegraph index` tree-sitter 全仓索引 → STEP4 函数语义中文化 → STEP5 `insert_custom_kg` 注入 LightRAG → STEP6 git commit 增量更新。

#### Scenario: 首次克隆构建
- **WHEN** 用户提交 repo + branch
- **THEN** opencode 拉取代码到工作区，在仓库根目录执行 `codegraph init -i` 与 `codegraph index`，tree-sitter 解析全仓生成符号/边写入 SQLite，六步流水线完整跑通

#### Scenario: 增量重建
- **WHEN** git commit 后代码变更
- **THEN** 仅重建受影响子树（按变更文件集计算依赖符号），不进行全量重建，重建耗时显著低于全量构建

#### Scenario: 大仓库构建超时兜底
- **WHEN** 大型仓库（如 VS Code ~10k 文件）全量构建超过单次构建预算/超时阈值
- **THEN** 自动降级为增量构建 + 缓存预热策略，复用上次已索引子树，输出部分可用图谱并标记未完成范围

#### Scenario: 构建中断恢复
- **WHEN** 构建过程中进程被中断（OOM/手动 kill/断电）
- **THEN** 重启后基于 SQLite 已持久化的部分图谱断点续建，而非从零全量重建

#### Scenario: 构建失败错误上报
- **WHEN** tree-sitter 解析某文件失败或 SQLite 写入异常
- **THEN** 记录失败文件与原因到构建日志，跳过该文件继续构建，最终汇总失败清单供人工修复

### Requirement: MCP 工具查询能力
The system SHALL 经 CodeGraph MCP Server 暴露以下查询：调用者查询 `codegraph_callers(symbol, depth)`、被调用者查询 `codegraph_callees(symbol)`、图谱探索 `codegraph_explore(symbol)`、污点分析 `codegraph_taint(entry, sink)`，返回字段含 fan_in/fan_out/圈复杂度/方法数/安全发现。

#### Scenario: 调用关系查询
- **WHEN** A2 代码分析沿报错栈做静态污点追踪，调用 codegraph_callers(target_fn, depth=3)
- **THEN** 返回目标函数多层调用者列表，含每层 fan_in/fan_out/圈复杂度，构建静态嫌疑函数集 S_static

#### Scenario: 污点追踪查询
- **WHEN** 调用 codegraph_taint(entry="user_input", sink="sql_exec")
- **THEN** 返回从 entry 到 sink 的污点传播路径（经调用边串联），标注每跳的函数与行号

#### Scenario: 跨语言调用链断裂桥接
- **WHEN** Java 调用 Python 经 RPC/FFI 等跨语言边界，调用链在图谱中断裂
- **THEN** 插入桥接节点（bridge node）标记断裂点，并触发人工标注回流补全跨语言边

#### Scenario: 不存在符号查询报错
- **WHEN** 查询的 symbol 在图谱中不存在
- **THEN** 返回结构化错误「symbol not found」及相近符号建议列表，而非空结果或异常

#### Scenario: 图谱子图探索
- **WHEN** 调用 codegraph_explore(symbol) 探索某符号的局部子图
- **THEN** 返回以该符号为中心的 N 跳邻居子图（节点 + 边），供可视化或上下文注入

### Requirement: SQLite 图谱存储与本地运行
The system SHALL 将符号与调用/继承/引用边存储于本地 SQLite（`.codegraph/graph.db`，含 FTS5 全文搜索与关系图），100% 本地运行，无需外部图数据库（Neo4j 可选）。

#### Scenario: 离线本地运行
- **WHEN** 无网络环境
- **THEN** 已构建的图谱仍可被 MCP 工具查询，离线能力可用，无任何远程依赖

#### Scenario: FTS5 全文搜索
- **WHEN** 按中文函数语义或符号关键词检索
- **THEN** 经 SQLite FTS5 全文索引命中相关符号节点，返回匹配节点列表

#### Scenario: 大文件量查询性能
- **WHEN** 在 ~10k 文件量级图谱上执行多跳调用链查询
- **THEN** 单次查询响应在可接受阈值内（秒级），无需读取原始源文件

#### Scenario: DB 损坏恢复
- **WHEN** .codegraph/graph.db 文件损坏或锁死
- **THEN** 提供重建命令基于源码重新索引生成新 DB，并备份旧 DB 供诊断

### Requirement: 多语言支持与基准性能
The system SHALL 通过 CodeGraph 的 tree-sitter 支持 20+ 编程语言，并在大型仓库（如 VS Code ~10k 文件）下 agent 几乎零文件读取即可回答架构问题，构建峰值内存约 3.2GB。

#### Scenario: 多语言代码仓
- **WHEN** 代码仓包含 Java/Python/Go/TypeScript/Rust 等多语言文件
- **THEN** tree-sitter 分别解析各语言 AST，统一提取符号与边存入同一图谱，支持 20+ 语言

#### Scenario: 大仓库零文件读取
- **WHEN** 在 ~10k 文件量级仓库上由 agent 回答架构问题
- **THEN** agent 经 MCP 工具查询图谱即可回答，几乎不读取原始源文件

#### Scenario: 构建峰值内存可控
- **WHEN** 大仓库全量索引构建
- **THEN** 构建峰值内存约 3.2GB，不因仓库规模线性膨胀导致 OOM

#### Scenario: 基准收益达标
- **WHEN** 与 V2.0 手写 tree-sitter 方案对比基准（7 个真实开源仓库中位数）
- **THEN** 平均节省 35% 成本 / 59% token / 49% 耗时 / 70% 工具调用

## 技术细节

### 接口定义

MCP 工具签名（参数 + 返回）：

```typescript
codegraph_callers(symbol: string, depth: number = 2): {
  callers: Array<{
    symbol: string;
    type: "function" | "class" | "method";
    file: string;
    line: number;
    fan_in: number;
    fan_out: number;
    complexity: number;
    methods?: number;
    security_findings?: string[];
  }>;
  edges: Array<{ src: string; tgt: string; type: "call"; weight: number }>;
  truncated: boolean;
}

codegraph_callees(symbol: string): {
  callees: Array<{
    symbol: string;
    type: "function" | "class" | "method";
    file: string;
    line: number;
    fan_in: number;
    fan_out: number;
    complexity: number;
  }>;
  edges: Array<{ src: string; tgt: string; type: "call"; weight: number }>;
}

codegraph_explore(symbol: string): {
  nodes: Array<{
    symbol: string;
    type: "function" | "class" | "method";
    file: string;
    line: number;
    fan_in: number;
    fan_out: number;
    complexity: number;
    cn_summary?: string;
  }>;
  edges: Array<{
    src: string;
    tgt: string;
    type: "call" | "inherit" | "ref";
    weight: number;
  }>;
  center: string;
}

codegraph_taint(entry: string, sink: string): {
  paths: Array<{
    hops: Array<{
      symbol: string;
      file: string;
      line: number;
      type: "call" | "ref";
    }>;
    reachable: boolean;
  }>;
  entry_found: boolean;
  sink_found: boolean;
}
```

### 数据结构

CPG 节点 schema：

```json
{
  "symbol": "UserService.createUser",
  "type": "method",
  "file": "src/service/UserService.java",
  "line": 42,
  "fan_in": 7,
  "fan_out": 12,
  "complexity": 8,
  "cn_summary": "用户创建服务：校验入参与唯一性后持久化用户记录并发布事件"
}
```

CPG 边 schema：

```json
{
  "src": "UserService.createUser",
  "tgt": "UserRepository.save",
  "type": "call",
  "weight": 1.0
}
```

SQLite schema（`.codegraph/graph.db`，FTS5 全文搜索 + 关系图）：

```sql
CREATE TABLE nodes (
  id          INTEGER PRIMARY KEY,
  symbol      TEXT NOT NULL UNIQUE,
  type        TEXT CHECK(type IN ('function','class','method')),
  file        TEXT NOT NULL,
  line        INTEGER NOT NULL,
  fan_in      INTEGER DEFAULT 0,
  fan_out     INTEGER DEFAULT 0,
  complexity  INTEGER DEFAULT 0,
  cn_summary  TEXT
);

CREATE TABLE edges (
  id      INTEGER PRIMARY KEY,
  src_id  INTEGER NOT NULL REFERENCES nodes(id),
  tgt_id  INTEGER NOT NULL REFERENCES nodes(id),
  type    TEXT CHECK(type IN ('call','inherit','ref')),
  weight  REAL DEFAULT 1.0
);
CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_tgt ON edges(tgt_id);

CREATE VIRTUAL TABLE nodes_fts USING fts5(
  symbol, cn_summary, file,
  content='nodes', content_rowid='id'
);
```

### 配置项

opencode.json 完整结构：

```json
{
  "plugin": ["opencode-codegraph"],
  "mcp": {
    "codegraph": {
      "command": "codegraph",
      "args": ["mcp"],
      "env": {
        "CODEGRAPH_DB": "./.codegraph/graph.db"
      }
    }
  }
}
```

npm 包：`opencode-codegraph@0.1.38`

安装命令（Windows 与 Linux 等价）：

```bash
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | bash
```

克隆后自动构建伪流程：

```bash
git clone -b $branch $repo ./workspace/$repo_name
cd ./workspace/$repo_name
codegraph init -i
codegraph index
```

## 验收指标
- 平均节省 35% 成本 / 59% token / 49% 耗时 / 70% 工具调用（7 个真实开源仓库中位数基准）。
- 大型仓库（~10k 文件）下 agent 回答架构问题几乎零文件读取。
- 支持 20+ 编程语言（tree-sitter 语法覆盖）。
- 100% 本地运行，无网络依赖，离线可查询。
- 构建峰值内存约 3.2GB，大仓库不 OOM。
- 增量重建耗时显著低于全量构建（受影响子树仅重建变更集）。
- `.codegraph/graph.db` 含 FTS5 全文搜索与关系图，单次多跳查询秒级响应。

## UT 测试方案

测试框架约定：Python `pytest` + `pytest-asyncio`；SQLite 统一使用 `:memory:` 临时库；MCP 工具传输层用 mock；多语言解析使用真实代码样本（不 mock tree-sitter 语法解析）。覆盖目标：line + branch 覆盖率 ≥85%。

| # | 用例名 | 被测组件 | 输入 | 预期输出 | mock 策略 |
|---|---|---|---|---|---|
| 1 | `test_tree_sitter_multi_lang` | tree-sitter 多语言解析层（CodeGraph 解析器 / `codegraph index` 符号产出） | 6 份真实代码样本（Java/Go/Python/TypeScript/JavaScript/C++ 各一份，每份含函数、类、方法定义） | 每种语言均正确解析 AST，提取出的函数/类/方法符号数量与样本实际定义数一致，节点 type 字段取值在 `function`/`class`/`method` 枚举内 | 不 mock tree-sitter（真实语法解析）；mock 文件系统 IO 与 SQLite 写入层（落 `:memory:`） |
| 2 | `test_symbol_extraction` | 符号提取器（symbol extractor） | 含函数定义、类定义、方法定义的混合样本代码（带参数列表与返回类型注解） | 提取符号含正确的函数名、类名、参数列表（含类型与顺序）、返回类型、所在文件路径与起始行号 | mock SQLite 持久化层，断言传入节点对象的字段值而非 DB 落库结果 |
| 3 | `test_edge_extraction` | 边提取器（edge extractor） | 含函数调用、变量数据流、控制流（if/for/while）、类继承的样本代码 | 正确提取 call 边、dataflow 边、control 边、inheritance(inherit) 边，`src`/`tgt`/`type`/`weight` 字段正确且 src→tgt 方向无误 | mock 节点查询层（提供固定 symbol→id 映射表），真实运行边提取逻辑 |
| 4 | `test_sqlite_crud` | SQLite 存储层（nodes/edges 表 CRUD + 事务） | `:memory:` 临时 SQLite 库 + 一组节点/边样本 | 插入、查询、更新、删除均成功；唯一约束（symbol UNIQUE）重复插入触发冲突；显式事务回滚后数据恢复原状，无脏写 | 不 mock（真实 SQLite `:memory:`）；mock 上游 CPG 构建产物作为输入数据 |
| 5 | `test_fts5_fulltext_search` | FTS5 全文检索层（nodes_fts 虚拟表） | 含中文 cn_summary 与英文符号名的节点集合 + 精确/模糊/中文检索关键词 | 符号名模糊匹配命中、中文摘要检索命中、结果按 BM25 相关度排序；无命中时返回空列表而非异常 | 不 mock（真实 FTS5 `:memory:`）；mock 节点填充数据构造（注入中英文摘要） |
| 6 | `test_mcp_callers_tool` | MCP `codegraph_callers` 工具适配层 + 反向 BFS | 图谱含 A 被 B、C 调用，B 又被 D 调用；调用 `codegraph_callers("A", depth=3)` | 返回多层反向调用者列表（D→B→A 顺序），每层含 fan_in/fan_out/complexity/methods/security_findings；`truncated` 标记正确 | mock MCP 传输层（工具入参/出参序列化）；使用真实 `:memory:` 图谱数据 |
| 7 | `test_mcp_callees_tool` | MCP `codegraph_callees` 工具适配层 | 图谱含 A 调用 B、C，B 调用 E；调用 `codegraph_callees("A")` | 返回正向被调用者列表（B、C）及 call 边（A→B、A→C） | mock MCP 传输层；真实 `:memory:` 图谱 |
| 8 | `test_mcp_explore_tool` | MCP `codegraph_explore` 工具适配层 | 以符号 X 为中心的局部子图（含 call/inherit/ref 三类边） | 返回 N 跳邻居子图（nodes + edges），`center` = X，边类型含 call/inherit/ref | mock MCP 传输层；真实 `:memory:` 图谱 |
| 9 | `test_mcp_taint_tool` | MCP `codegraph_taint` 工具适配层 | 图谱含 source=user_input 经函数链传播到 sink=sql_exec 的 dataflow/ref 边；调用 `codegraph_taint("user_input","sql_exec")` | 返回传播路径（每跳 symbol/file/line/type），`reachable=true`，`entry_found=true`，`sink_found=true` | mock MCP 传输层；真实 `:memory:` 图谱（含 dataflow/ref 边） |
| 10 | `test_large_repo_incremental` | 增量构建调度器（git diff 子树重建） | 已索引 ~10k 文件仓库 + git diff 产生的变更文件集（仅 2 文件变更） | 仅重建变更文件依赖的符号子树；未变更节点 mtime/哈希不变；重建耗时远低于全量构建 | mock git diff 输出；mock 文件系统（合成目录树模拟 10k 文件）；真实运行子树重建逻辑 |
| 11 | `test_memory_peak` | 构建内存监控（peak RSS 采集，tracemalloc/resource） | 大仓库构建过程（合成 ~10k 符号图谱流） | 构建峰值内存 ≤3.2GB；tracemalloc/resource 采样峰值断言通过，无 OOM | mock 真实源码解析为合成节点流（避免拉取真实大仓）；真实采集内存指标 |
| 12 | `test_opencode_json_config` | opencode.json 插件配置加载与校验器 | 合法/非法 opencode.json 样本（含/缺 plugin、mcp.codegraph、版本号、CODEGRAPH_DB） | 合法配置加载成功并校验 command/args/CODEGRAPH_DB 与版本号；非法配置返回明确结构化校验错误 | mock 文件读取（注入 JSON 字符串）；不依赖真实文件系统 |

## E2E 测试方案

端到端测试基于真实 git 仓库与真实 codegraph CLI，验证「构建 → 持久化 → MCP 查询 → 增量重建」全链路闭合。每个场景独立编排，断言点量化可测。

| # | 场景名 | 前置条件 | 测试步骤 | 预期结果 | 断言点 |
|---|---|---|---|---|---|
| 1 | `e2e_full_build_pipeline` | 一个含 Java/Go/Python 源码的小型真实 git 仓库可用；codegraph CLI 已安装并版本兼容 | 1. `git clone` 仓库 → 2. `codegraph init -i` → 3. `codegraph index`（tree-sitter 解析→CPG 构建→SQLite 持久化）→ 4. 启动 codegraph MCP Server → 5. 调用 `codegraph_callers` 与 `codegraph_callees` | `graph.db` 生成且含 nodes/edges/nodes_fts 三表；MCP Server 就绪；callers/callees 返回非空且调用链闭合 | 三表均存在；MCP 健康检查通过；callers/callees 返回字段齐全；调用链 src↔tgt 在图谱中可双向验证 |
| 2 | `e2e_mcp_query_callers` | 全量构建完成，图谱含 D→B→A 反向调用链（depth=3） | 经 MCP 协议调用 `codegraph_callers("A", depth=3)` | 返回 D→B→A 多层调用者，每层含 fan_in/fan_out/complexity | 调用者层级深度=3；每层字段齐全；反向 BFS 顺序正确（D 先于 B）；`truncated` 取值合理 |
| 3 | `e2e_mcp_query_callees` | 全量构建完成，图谱含 A→B→E 正向调用链 | 经 MCP 协议调用 `codegraph_callees("A")` | 返回 A 的被调用者列表及 call 边 | callees 含 B；edges 含 A→B；正向链 A→B→E 闭合；无反向边混入 |
| 4 | `e2e_taint_analysis` | 图谱含 user_input → process() → sanitize() → sql_exec 的 dataflow/ref 边 | 经 MCP 调用 `codegraph_taint("user_input","sql_exec")` | 返回可达传播路径，每跳含 symbol/file/line，`reachable=true` | `entry_found=true`；`sink_found=true`；路径首跳=source、末跳=sink；`reachable=true`；路径不绕过 sink |
| 5 | `e2e_incremental_rebuild` | 全量构建完成，已记录全量节点哈希集 | 1. 记录全量节点哈希 → 2. `git commit` 修改 2 个文件 → 3. 触发增量重建 → 4. 比对重建前后节点哈希 | 仅受影响子树节点重建；未变更节点哈希不变；重建耗时显著低于全量 | 变更文件依赖符号被更新；未变更符号哈希前后一致；重建耗时 < 全量构建耗时；日志无全量重建痕迹 |
| 6 | `e2e_multi_language_repo` | 混合仓库含 Java+Go+Python 三语言源文件 | 1. 全量构建 → 2. 查询跨语言符号 → 3. 验证同图谱共存 | 三语言符号均写入同一 `graph.db`，跨语言 explore 可用 | 三语言各自符号数>0；nodes 表含三种语言的文件路径前缀；跨语言 `codegraph_explore` 返回非空子图 |

## 跨模块集成测试方案

集成测试聚焦 CodeGraph 与其上下游模块的「数据契约边界」，验证 CPG 节点/边、ast_kg JSON、MCP 响应在模块间传递时字段映射无损、方向正确。约定：跨模块边界用 mock 隔离真实下游进程；SQLite 一律 `:memory:` 真实库；tree-sitter 解析用真实样本不 mock 语法。

### 上下游依赖关系表

| 上游模块 | 数据契约（输入到 CodeGraph） | 下游模块 | 数据契约（CodeGraph 输出） |
|---|---|---|---|
| git repo（opencode 拉码 repo+branch） | repo URL + branch → 工作区源文件树（文件路径 + 内容） | 代码中文描述（code2cn） | symbol / file / source_code（函数符号 + 所在文件 + 源码片段） |
| opencode-codegraph 插件（tree-sitter @0.1.38） | AST 解析产物 → CPG 节点 `{symbol,type,file,line}` + CPG 边 `{source,target,type}` | LightRAG | ast_kg JSON `{entities,edges}` → `ainsert_custom_kg` |
| — | — | 5-Agent A2 | MCP 工具响应：callers/callees/explore/taint（fan_in/fan_out/complexity/paths） |
| — | — | 双图谱 | S_static：`func_id`/`func_name`/`call_path`/`static_depth`（函数级调用图） |

### 集成测试场景

#### integ_git_to_codegraph — git clone → tree-sitter 解析 → CPG 构建
- **涉及模块**：git repo、opencode、opencode-codegraph 插件、CodeGraph tree-sitter 解析层、CPG 构建器
- **集成点**：git clone 产物 → tree-sitter 文件输入；tree-sitter AST → CPG 节点/边
- **测试步骤**：1. mock git clone 将预设样本仓铺到临时目录；2. 触发 `codegraph init`/`index` 调用 tree-sitter 解析；3. 收集产出的 CPG 节点与边
- **预期结果**：每个源文件均被 tree-sitter 解析，CPG 节点 `symbol/file/line` 正确，边 `source→target` 方向无误
- **断言点**：节点数 = 样本实际定义数；边 `type` ∈ {call,dataflow,control,inheritance}；`file` 路径与 clone 产物一致；无未解析文件残留

#### integ_codegraph_to_code2cn — CodeGraph 符号节点 → code2cn 输入
- **涉及模块**：CodeGraph 节点输出层、代码中文描述（code2cn）
- **集成点**：CPG 函数节点 → code2cn 输入（`symbol`/`file`/`source_code` 三字段传递）
- **测试步骤**：1. 从 CPG 取函数节点；2. 按 code2cn 契约组装 `{symbol,file,source_code}`；3. 调用 code2cn（mock 下游）并回写 `cn_summary`
- **预期结果**：code2cn 收到完整三字段，返回 `cn_summary` 回写节点
- **断言点**：`symbol` 非空且与节点一致；`file` 为有效路径；`source_code` 非空且为函数体；回写后 `nodes.cn_summary` 非空；code2cn 被调用次数 = 函数节点数

#### integ_codegraph_to_lightrag_astkg — CPG → ast_kg JSON → LightRAG ainsert_custom_kg
- **涉及模块**：CPG → ast_kg 转换器、LightRAG `ainsert_custom_kg`
- **集成点**：CPG 节点/边 → ast_kg JSON `{entities,edges}` → `ainsert_custom_kg`
- **测试步骤**：1. 取 CPG 节点/边；2. 转换为 ast_kg JSON；3. 调用 LightRAG `ainsert_custom_kg`（mock 下游）
- **预期结果**：ast_kg JSON 结构合法，entities 含 `entity_name`/`description`/`source_code`，edges 含 `src_id`/`tgt_id`/`description`；`ainsert_custom_kg` 接收并确认
- **断言点**：entities 数 = CPG 节点数；edges 数 = CPG 边数；`entity_name` 与 `symbol` 一一映射；`ainsert_custom_kg` 被调用一次且参数非空

#### integ_codegraph_to_agent2_mcp — 5-Agent A2 通过 MCP 查询调用链
- **涉及模块**：5-Agent A2、CodeGraph MCP Server（callers/callees/taint）
- **集成点**：A2 经 MCP 协议调用 `codegraph_callers`/`callees`/`taint`
- **测试步骤**：1. A2 持报错栈符号；2. 调 `callers(depth=3)` 反向追溯；3. 调 `callees` 正向；4. 调 `taint(entry,sink)`；5. 组装 S_static
- **预期结果**：A2 收到调用链与污点路径，构建 S_static
- **断言点**：MCP tool call 参数序列化正确；callers 返回多层 + `truncated`；callees 返回正向 call 边；taint `reachable=true`；S_static 含 `func_id`/`func_name`/`call_path`/`static_depth`

#### integ_codegraph_to_dualgraph_static — CodeGraph 调用图 → S_static
- **涉及模块**：CodeGraph 调用图、双图谱 S_static
- **集成点**：CPG call 边 → S_static `{func_id,func_name,call_path,static_depth}`
- **测试步骤**：1. 取 CPG call 边子图；2. 投影为函数级调用图；3. 计算 `call_path` 与 `static_depth`
- **预期结果**：S_static 节点 = 函数集合，边 = call 边，`call_path` 闭合，`static_depth` 正确
- **断言点**：`func_id` 唯一；`func_name` 与 `symbol` 一致；`call_path` 路径节点连续无断；`static_depth` 与 BFS 层数一致

#### integ_full_pipeline_codegraph — git clone → CPG → SQLite → MCP → 下游消费全链路
- **涉及模块**：git repo、opencode-codegraph、CodeGraph 构建、SQLite、MCP Server、code2cn、LightRAG、双图谱
- **集成点**：全链路 clone → CPG → SQLite 持久化 → MCP 查询 → 下游消费
- **测试步骤**：1. mock clone；2. CPG 构建；3. SQLite 持久化；4. MCP 查询；5. 下游 code2cn/LightRAG/双图谱消费
- **预期结果**：全链路闭合，各环节产物可双向追溯
- **断言点**：`graph.db` 三表（nodes/edges/nodes_fts）存在；MCP 查询非空；code2cn 收到 `symbol/file/source_code`；LightRAG 收到 ast_kg；S_static 含完整调用链；全链路无字段丢失

## 测试数据与 Mock 规范

### 测试数据构造策略
- **多语言代码样本仓库**：`tests/fixtures/codegraph/samples/<lang>/*.ext`，每语言一个小型样本文件，含函数定义、类定义、方法定义、函数调用、类继承、变量数据流，供 tree-sitter 真实解析（不 mock 语法）。
- **Fixture 工厂**：`conftest.py` 提供参数化工厂函数 `build_cpg_nodes()`/`build_cpg_edges()`/`build_ast_kg()`/`build_mcp_response()`，按场景注入定制化测试数据。
- **conftest.py**：统一注册 pytest fixture（SQLite `:memory:` 初始化、Mock 注册、多语言样本加载、临时工作区），保证测试隔离与可复现。

### Mock 数据样本

CPG 节点样本 JSON（function/class/method 三种类型）：

```json
[
  {
    "symbol": "computeHash",
    "type": "function",
    "file": "src/util/hash.go",
    "line": 12,
    "fan_in": 3,
    "fan_out": 2,
    "complexity": 4,
    "cn_summary": "计算输入字节流的哈希摘要"
  },
  {
    "symbol": "UserService",
    "type": "class",
    "file": "src/service/UserService.java",
    "line": 8,
    "fan_in": 5,
    "fan_out": 9,
    "complexity": 0,
    "cn_summary": "用户领域服务：封装用户创建、查询、状态变更"
  },
  {
    "symbol": "UserService.createUser",
    "type": "method",
    "file": "src/service/UserService.java",
    "line": 42,
    "fan_in": 7,
    "fan_out": 12,
    "complexity": 8,
    "cn_summary": "用户创建服务：校验入参与唯一性后持久化用户记录并发布事件"
  }
]
```

CPG 边样本 JSON（call/dataflow/control/inheritance 四种类型）：

```json
[
  { "source": "UserService.createUser", "target": "UserRepository.save", "type": "call", "weight": 1.0 },
  { "source": "handleRequest", "target": "userInput", "type": "dataflow", "weight": 0.9 },
  { "source": "validateInput", "target": "returnError", "type": "control", "weight": 0.7 },
  { "source": "AdminUserService", "target": "UserService", "type": "inheritance", "weight": 1.0 }
]
```

ast_kg JSON 样本（entities + edges 完整结构，供 LightRAG 注入）：

```json
{
  "entities": [
    {
      "entity_name": "UserService.createUser",
      "type": "method",
      "description": "用户创建服务：校验入参与唯一性后持久化用户记录并发布事件",
      "source_code": "public User createUser(RegisterDTO dto) { ... }",
      "file_path": "src/service/UserService.java",
      "line_number": 42
    },
    {
      "entity_name": "UserRepository.save",
      "type": "method",
      "description": "持久化用户记录到数据库",
      "source_code": "public void save(User u) { ... }",
      "file_path": "src/repository/UserRepository.java",
      "line_number": 18
    }
  ],
  "edges": [
    {
      "src_id": "UserService.createUser",
      "tgt_id": "UserRepository.save",
      "description": "createUser 调用 save 持久化用户"
    }
  ]
}
```

MCP callers/callees/explore/taint 响应 Mock JSON：

```json
{
  "codegraph_callers": {
    "callers": [
      { "symbol": "AuthController.register", "type": "method", "file": "src/controller/AuthController.java", "line": 30, "fan_in": 2, "fan_out": 5, "complexity": 6 }
    ],
    "edges": [{ "src": "AuthController.register", "tgt": "UserService.createUser", "type": "call", "weight": 1.0 }],
    "truncated": false
  },
  "codegraph_callees": {
    "callees": [
      { "symbol": "UserRepository.save", "type": "method", "file": "src/repository/UserRepository.java", "line": 18, "fan_in": 4, "fan_out": 1, "complexity": 2 }
    ],
    "edges": [{ "src": "UserService.createUser", "tgt": "UserRepository.save", "type": "call", "weight": 1.0 }]
  },
  "codegraph_explore": {
    "nodes": [
      { "symbol": "UserService.createUser", "type": "method", "file": "src/service/UserService.java", "line": 42, "fan_in": 7, "fan_out": 12, "complexity": 8, "cn_summary": "用户创建服务" }
    ],
    "edges": [{ "src": "UserService.createUser", "tgt": "UserRepository.save", "type": "call", "weight": 1.0 }],
    "center": "UserService.createUser"
  },
  "codegraph_taint": {
    "paths": [
      {
        "hops": [
          { "symbol": "user_input", "file": "src/controller/AuthController.java", "line": 20, "type": "ref" },
          { "symbol": "UserService.createUser", "file": "src/service/UserService.java", "line": 42, "type": "call" },
          { "symbol": "sql_exec", "file": "src/repository/UserRepository.java", "line": 22, "type": "ref" }
        ],
        "reachable": true
      }
    ],
    "entry_found": true,
    "sink_found": true
  }
}
```

多语言代码样本（Java/Go/Python/TS/JS/C++ 各一个小型样本文件）：

```java
// tests/fixtures/codegraph/samples/java/UserService.java
public class UserService {
  public User createUser(RegisterDTO dto) {      // method
    validate(dto);
    return repository.save(dto.toUser());
  }
  private void validate(RegisterDTO dto) { }      // method
}
```

```go
// tests/fixtures/codegraph/samples/go/hash.go
func computeHash(data []byte) string {            // function
  h := sha256.Sum256(data)
  return hex.EncodeToString(h[:])
}
```

```python
# tests/fixtures/codegraph/samples/python/handler.py
def handle_request(req):                          # function
    user_input = req.get("name")
    return process(user_input)

class Handler:                                    # class
    def run(self):                                # method
        pass
```

```typescript
// tests/fixtures/codegraph/samples/ts/service.ts
class OrderService {                              // class
  create(item: Item): Order {                     // method
    return this.repo.save(item);
  }
}
```

```javascript
// tests/fixtures/codegraph/samples/js/utils.js
function parseConfig(raw) {                       // function
  return JSON.parse(raw);
}
```

```cpp
// tests/fixtures/codegraph/samples/cpp/engine.cpp
class Engine {                                    // class
public:
  void start();                                   // method
};
int compute(int x) { return x * 2; }              // function
```

opencode.json 插件配置 Mock：

```json
{
  "plugin": ["opencode-codegraph"],
  "mcp": {
    "codegraph": {
      "command": "codegraph",
      "args": ["mcp"],
      "env": { "CODEGRAPH_DB": "./.codegraph/graph.db" }
    }
  }
}
```

### Mock 规范
- **git repo mock**：预设仓库路径（fixtures 目录作为伪 clone 产物）+ mock clone，跳过真实网络与磁盘克隆，直接返回样本文件树。
- **opencode-codegraph 插件 mock**：mock tree-sitter 解析输出（返回固定 AST/符号/边），用于不依赖真实 CLI 的单测；集成测试改用真实插件以验证端到端解析。
- **SQLite**：用 `:memory:` 临时库（**不 mock**，真实 FTS5），保证存储层与全文检索行为真实可测。
- **MCP 传输层 mock**：mock MCP 协议序列化层，验证 tool call 入参与响应格式，**不产生真实进程间通信**。

### 测试数据库初始化
SQLite `:memory:` + FTS5 虚拟表创建 DDL（测试专用，含 dataflow/control 边类型以覆盖四类边）：

```sql
-- 测试库初始化（:memory:）
CREATE TABLE nodes (
  id          INTEGER PRIMARY KEY,
  symbol      TEXT NOT NULL UNIQUE,
  type        TEXT CHECK(type IN ('function','class','method')),
  file        TEXT NOT NULL,
  line        INTEGER NOT NULL,
  fan_in      INTEGER DEFAULT 0,
  fan_out     INTEGER DEFAULT 0,
  complexity  INTEGER DEFAULT 0,
  cn_summary  TEXT
);

CREATE TABLE edges (
  id      INTEGER PRIMARY KEY,
  src_id  INTEGER NOT NULL REFERENCES nodes(id),
  tgt_id  INTEGER NOT NULL REFERENCES nodes(id),
  type    TEXT CHECK(type IN ('call','inherit','ref','dataflow','control')),
  weight  REAL DEFAULT 1.0
);
CREATE INDEX idx_edges_src ON edges(src_id);
CREATE INDEX idx_edges_tgt ON edges(tgt_id);

CREATE VIRTUAL TABLE nodes_fts USING fts5(
  symbol, cn_summary, file,
  content='nodes', content_rowid='id'
);
```

### Fixture 文件组织

```text
tests/fixtures/codegraph/
├── samples/
│   ├── java/        UserService.java
│   ├── go/          hash.go
│   ├── python/      handler.py
│   ├── ts/          service.ts
│   ├── js/          utils.js
│   └── cpp/         engine.cpp
├── cpg_nodes.json          # CPG 节点样本（function/class/method）
├── cpg_edges.json          # CPG 边样本（call/dataflow/control/inheritance）
├── ast_kg.json             # ast_kg 样本（entities + edges）
├── mcp_responses.json      # MCP callers/callees/explore/taint 响应 Mock
└── opencode.json           # 插件配置 Mock
```

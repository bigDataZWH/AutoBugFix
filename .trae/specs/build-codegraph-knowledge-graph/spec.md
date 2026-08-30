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

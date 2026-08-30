# AutoBugFix — 问题单自动定位·出方案·修复 Agent 设计文档

> 版本：v1.0（最终方案）
> 部署环境：Windows 11 + WSL2 + OpenCode 订阅
> 架构形态：自研编排层（核心资产） + FixExecutor 抽象层 + OpenCode headless 执行层（复用成熟基建）

---

## 目录

1. [设计目标与原则](#1-设计目标与原则)
2. [业界最佳实践与关键洞察](#2-业界最佳实践与关键洞察)
3. [整体架构](#3-整体架构)
4. [核心模块详细设计](#4-核心模块详细设计)
5. [FixExecutor 执行抽象层](#5-fixexecutor-执行抽象层)
6. [OpenCode 集成实现](#6-opencode-集成实现)
7. [代码智能层](#7-代码智能层)
8. [验证护栏](#8-验证护栏)
9. [Win11 部署方案](#9-win11-部署方案)
10. [演进路线](#10-演进路线)
11. [Phase 1 周粒度任务拆解](#11-phase-1-周粒度任务拆解)
12. [评估与度量](#12-评估与度量)
13. [风险与对策](#13-风险与对策)

---

## 1. 设计目标与原则

### 1.1 目标

实现一个 Agent，能够自动完成问题单的 **根因定位 → 方案生成 → 代码修复 → 验证** 全流程，最终产出可审核的 PR。

### 1.2 设计原则

| 原则 | 含义 |
|------|------|
| **自研聚焦壁垒** | 根因定位与验证护栏是差异化核心，必须自研并深度优化 |
| **复用成熟基建** | 代码修改执行、LSP、Git 快照等已被大规模验证的基建，复用 OpenCode，不重复造轮子 |
| **抽象层解耦** | 通过 FixExecutor 接口隔离编排层与执行层，保留切换/并跑执行器的自由度 |
| **证据驱动** | 每个定位结论、每个补丁必须有可追溯证据，禁臆测 |
| **三重护栏** | 编译门 + 测试门 + 静态门，硬阻断幻觉修复 |
| **状态机驱动** | 每阶段准出条件明确，失败回退而非一路到底 |
| **Human-in-the-Loop** | 高风险变更强制人工审核 |

---

## 2. 业界最佳实践与关键洞察

### 2.1 主流框架对比

| 框架 | 类型 | 核心特点 | 适配点 |
|------|------|----------|--------|
| Devin (Cognition) | 全栈 Agent | 长程规划、状态机、浏览器/终端 | 端到端参考 |
| OpenHands/OpenDevin | 开源 Agent | 事件流、Sandbox、可插拔 | 沙箱设计参考 |
| Agentless | 无 Agent | 定位→修复两阶段、纯工具调用 | 定位策略参考 |
| AutoCodeRover | 检索式 Agent | 频谱+AST+语义检索、DFS 导航 | 大仓库定位参考 |
| Aider | Pair Programmer | Git 集成、Repo Map、Edit Formats | 编辑格式参考 |
| **OpenCode** | 开源 Agent | **内置 LSP、Git 快照、headless run/serve、MCP** | **执行层复用** |

### 2.2 三条核心洞察

1. **Agent Harness 定律**：Agent 性能中 **98.4% 取决于基础设施**（工具/沙箱/上下文/循环控制），仅 1.6% 来自 LLM 本身。→ **把工程基建做厚**。

2. **定位是瓶颈**：SWE-bench 失败 case 中 **定位错误占 60%+**。→ **根因定位是差异化核心**。

3. **护栏比自由度更重要**：三重护栏（检索脚手架 + 编译门 + 测试门）能将幻觉修复率从 ~40% 降到 <10%。→ **约束 LLM 行动空间，强制证据闭环**。

### 2.3 方案选型结论

经全自研方案与 OpenCode 集成方案对比（详见对比卡片），确定采用 **混合演进方案**：

- 以 OpenCode 集成快速起步（2-4 周 MVP）
- 用 FixExecutor 抽象层保留演进自由度
- 自研火力集中在根因定位与验证护栏
- 长期沉淀核心资产，可随时切换/并跑自研执行器

---

## 3. 整体架构

### 3.1 架构总览

系统分为三层 + 一抽象：

```
┌─────────────────────────────────────────────────────────────────┐
│  自研编排层（LangGraph 状态机）— 核心资产                         │
│  问题理解 → 根因定位 → 方案编排 → 验证护栏                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ FixExecutor 接口
┌──────────────────────────┴──────────────────────────────────────┐
│  FixExecutor 执行抽象层（解耦：可切换/并跑执行器）                │
│  OpenCodeExecutor(默认) | NativeExecutor(自研·预留) | DiffAuditor│
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────┐
│  修复执行层（复用成熟基建）                                       │
│  OpenCode headless | LSP 语义导航 | 终端/沙箱 | Git 快照          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  支撑能力层                                                       │
│  代码智能服务(向量·BM25·调用图) | 记忆与知识库 | Win11 运行环境   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心流水线（状态机）

```
RECEIVED → UNDERSTOOD → REPRODUCED → LOCALIZED → PATCHED → VERIFIED → NEEDS_REVIEW → MERGED
                ↑                              ↓             ↓
                └──── BLOCKED ◀──── 失败重试 ◀─── 验证失败(≤K轮)
```

每阶段设准出条件，不满足则回退或转人工。

### 3.3 端到端数据流

```
问题单(ID/标题/描述/附件/日志/堆栈)
   │
   ▼
[问题理解] → IssueProfile(类型·严重度·信号·复现可行性)
   │
   ▼
[根因定位] ← 代码智能服务(向量+BM25+AST+LSP) → 嫌疑文件→符号→行 + 证据
   │
   ▼
[方案编排] → 修复假设 + PatchPolicy 约束
   │
   ▼  ── FixExecutor.execute() ──→
   │                          [OpenCode headless]
   │                          ├ Build Agent 接收定位证据
   │                          ├ LSP 导航验证符号
   │                          ├ 生成补丁(多候选)
   │                          ├ Git 快照
   │                          └ 返回 FixResult(diff+日志)
   │  ◀── FixResult 回传 ──────
   ▼
[DiffAuditor] → 文件白名单审计 + 禁用模式检测
   │
   ▼
[验证护栏] → 编译门 / 测试门 / 静态门
   ├ PASS → 生成 PR(根因/方案/证据/测试)
   └ FAIL → FixExecutor.iterate(session) 迭代(≤3轮) → BLOCKED/NEEDS_REVIEW
```

---

## 4. 核心模块详细设计

### 4.1 问题理解与分类模块

**职责**：把自然语言问题单结构化为 `IssueProfile`。

**关键设计**：
- **分类树**：Bug（逻辑/并发/资源泄漏/边界/兼容）/ Feature / 配置 / 文档 / 性能
- **信号提取器**：从描述/日志/堆栈抽取 stack frame、error code、版本号、环境、触发条件
- **严重度评估**：影响面（crash/data loss/security）× 复现频率
- **复现可行性判定**：决定走"自动复现"还是"假设驱动"

**输出 schema**：

```json
{
  "issue_id": "ISS-1234",
  "type": "bug",
  "subtype": "concurrency",
  "severity": "P1",
  "signals": {
    "stack_frames": ["com.app.Service.handle(:88)", "com.app.Pool.acquire(:42)"],
    "error_codes": ["DEADLOCK_DETECTED"],
    "keywords": ["连接池", "超时", "并发"],
    "env": {"lang": "java", "version": "17", "os": "linux"}
  },
  "reproducible": true,
  "repro_hints": "高并发下连续调用 acquire()，约 50 QPS 触发",
  "suspected_areas": [
    {"path": "src/main/com/app/Pool.java", "reason": "堆栈顶层", "confidence": 0.85}
  ]
}
```

### 4.2 根因定位模块（核心差异化）

采用 **"检索 + 导航 + 证据"三层定位**。

**第一层：文件级定位（Where）**
- 向量检索：IssueProfile + 信号向量召回 Top-K 文件（bge-m3 / text-embedding-3）
- 关键词 BM25：精确匹配 error code、函数名、类名
- 召回融合：RRF（Reciprocal Rank Fusion）合并
- Git 历史信号：recently changed files、blame

**第二层：符号级定位（What）**
- Tree-sitter 增量解析：构建 AST，定位到类/函数/方法
- LSP 语义导航：go-to-definition / find-references / hover，跨文件追踪调用链
- 调用图分析：从堆栈帧/可疑入口 DFS 遍历调用链

**第三层：行级定位（Which line）**
- 在嫌疑符号内，LLM 结合上下文给出精确行号 + 证据引用
- 证据闭环：每个结论附带 (代码引用, 推理依据, 置信度)

**护栏**：
- 定位结论必须可被 LSP/AST 验证（行号存在、符号确实在该文件）
- 置信度 < 阈值 → 触发第二轮扩展检索或转人工

**输出 schema**：

```json
{
  "issue_id": "ISS-1234",
  "localized_files": [
    {
      "path": "src/main/com/app/Pool.java",
      "symbols": [
        {"name": "acquire", "type": "method", "lines": [38, 45], "confidence": 0.85}
      ],
      "evidence": [
        {"code": "synchronized(lock) { ... }", "reason": "双重锁导致死锁", "ref": "Pool.java:40-44"}
      ]
    }
  ],
  "call_chain": ["Service.handle → Pool.acquire → Lock.wait"],
  "overall_confidence": 0.82
}
```

### 4.3 方案编排模块

**职责**：基于定位证据，组装修复指令与约束，交付 FixExecutor。

**关键设计**：
- **修复假设先行**：先输出"为什么坏 + 怎么修"的自然语言假设，再写代码
- **PatchPolicy 约束**：硬约束补丁范围
- **多候选策略**：可并行触发多个 OpenCode 会话生成候选

**PatchPolicy**：

```json
{
  "max_files": 5,
  "max_changed_lines": 300,
  "allow_dependency_change": false,
  "allow_test_deletion": false,
  "allow_config_change": "review_only",
  "file_whitelist": ["src/main/com/app/Pool.java", "src/main/com/app/Service.java"],
  "forbidden_diff_patterns": [
    "@pytest.mark.skip",
    "pytest.skip(",
    "|| true",
    "continue-on-error: true",
    "except: pass",
    "except Exception: pass"
  ]
}
```

---

## 5. FixExecutor 执行抽象层

### 5.1 设计目的

隔离编排层与执行层。编排层只依赖 `FixExecutor` 接口，不感知底层是 OpenCode 还是自研执行器。这样：

- 执行层可切换（OpenCode → 自研）
- 可并跑（多执行器对比选优）
- 迁移成本被控制在接口层内

### 5.2 接口契约

```python
from typing import Protocol, Literal
from pydantic import BaseModel


class FixRequest(BaseModel):
    issue_profile: dict
    localization: dict
    fix_hypothesis: str
    patch_policy: dict
    repo_path: str
    session_id: str | None = None
    feedback: str | None = None


class FixResult(BaseModel):
    success: bool
    session_id: str
    diff: str
    changed_files: list[str]
    tool_calls: list[dict]
    snapshot_ref: str
    executor_log: str
    error: str | None = None


class FixExecutor(Protocol):
    def execute(self, request: FixRequest) -> FixResult:
        ...

    def iterate(self, request: FixRequest, feedback: str) -> FixResult:
        ...

    def rollback(self, session_id: str, snapshot_ref: str) -> bool:
        ...

    def health_check(self) -> bool:
        ...
```

### 5.3 DiffAuditor（执行后审计）

在 FixExecutor 返回后、验证护栏前，对 diff 做独立审计：

```python
class DiffAuditor:
    def audit(self, diff: str, policy: PatchPolicy) -> AuditReport:
        violations = []
        for pattern in policy.forbidden_diff_patterns:
            if pattern in diff:
                violations.append(f"禁用模式命中: {pattern}")
        changed = parse_changed_files(diff)
        for f in changed:
            if f not in policy.file_whitelist:
                violations.append(f"越界修改: {f}")
        if len(changed) > policy.max_files:
            violations.append(f"文件数超限: {len(changed)} > {policy.max_files}")
        return AuditReport(passed=len(violations) == 0, violations=violations)
```

审计失败 → 直接拒绝，不进入验证阶段，反馈给 FixExecutor 迭代。

---

## 6. OpenCode 集成实现

### 6.1 OpenCode 能力映射

| 原需自研模块 | OpenCode 能力 | 复用方式 |
|------------|--------------|----------|
| LSP 语义导航 | 内置 LSP（20+ 语言，实时诊断反馈） | 启用 `OPENCODE_EXPERIMENTAL_LSP_TOOL` |
| 补丁生成 | Build Agent（文件读写、跨文件编辑） | `opencode run --agent issue-fixer` |
| 终端/沙箱 | 内置终端工具 | 编排层验证阶段调用 |
| Git 集成 | Git 快照 + `/undo` `/redo` | 利用回滚做验证失败回退 |
| 多候选 | 多会话并行 | 并行 `opencode run` |

### 6.2 三种集成通道

**通道 1：`opencode run`（单次调用）** — 补丁生成阶段

```bash
opencode run --model anthropic/claude-sonnet-4 \
  --agent issue-fixer \
  --format json \
  "{修复指令 + 定位证据 + PatchPolicy}"
```

**通道 2：`opencode serve`（常驻后端）** — 多轮迭代修复

```bash
opencode serve --port 4096

opencode run --attach http://localhost:4096 \
  --session {session_id} \
  --format json \
  "上一轮验证失败: {test_output}. 请修正补丁。"
```

**通道 3：MCP Server 桥接** — 自研代码智能层反向注入

```bash
opencode mcp add code-intelligence \
  --command "node" \
  --args "mcp-code-intel-server.js"
```

让 Build Agent 可主动检索定位证据、查找调用链。

### 6.3 OpenCodeExecutor 实现骨架

```python
import subprocess, json, os
from pathlib import Path


class OpenCodeExecutor:
    def __init__(self, serve_url: str = "http://localhost:4096", model: str = "anthropic/claude-sonnet-4"):
        self.serve_url = serve_url
        self.model = model
        self._ensure_serve()

    def _ensure_serve(self):
        if not self._probe_serve():
            subprocess.Popen(["opencode", "serve", "--port", "4096"], stdout=subprocess.DEVNULL)

    def _probe_serve(self) -> bool:
        try:
            subprocess.run(["opencode", "run", "--attach", self.serve_url, "--format", "json", "ping"],
                           capture_output=True, timeout=10, check=True)
            return True
        except Exception:
            return False

    def execute(self, request: FixRequest) -> FixResult:
        prompt = self._build_prompt(request)
        cmd = ["opencode", "run", "--attach", self.serve_url,
               "--model", self.model, "--agent", "issue-fixer", "--format", "json"]
        if request.session_id:
            cmd += ["--session", request.session_id]
        cmd += [prompt]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=request.repo_path, timeout=600)
        return self._parse(proc.stdout, request)

    def iterate(self, request: FixRequest, feedback: str) -> FixResult:
        request.feedback = feedback
        return self.execute(request)

    def rollback(self, session_id: str, snapshot_ref: str) -> bool:
        cmd = ["opencode", "run", "--attach", self.serve_url, "--session", session_id, "/undo"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode == 0

    def _build_prompt(self, request: FixRequest) -> str:
        parts = [
            f"问题: {request.issue_profile.get('issue_id')}",
            f"修复假设: {request.fix_hypothesis}",
            f"定位证据: {json.dumps(request.localization, ensure_ascii=False)}",
            f"约束: {json.dumps(request.patch_policy, ensure_ascii=False)}",
        ]
        if request.feedback:
            parts.append(f"上轮反馈: {request.feedback}")
        return "\n".join(parts)

    def _parse(self, stdout: str, request: FixRequest) -> FixResult:
        data = json.loads(stdout)
        return FixResult(
            success=data.get("success", True),
            session_id=data.get("session_id", request.session_id or ""),
            diff=data.get("diff", ""),
            changed_files=data.get("changed_files", []),
            tool_calls=data.get("tool_calls", []),
            snapshot_ref=data.get("snapshot_ref", ""),
            executor_log=data.get("log", ""),
            error=data.get("error"),
        )
```

### 6.4 自定义 issue-fixer Agent

```json
{
  "$schema": "https://opencode.ai/config.json",
  "agent": "issue-fixer",
  "model": "anthropic/claude-sonnet-4",
  "system_prompt": "你是问题修复 Agent。\n规则:\n1. 只修改定位证据指向的文件,不做无关重构\n2. 禁止添加 @pytest.mark.skip / pytest.skip / || true\n3. 禁止注释掉测试断言\n4. 禁止 except: pass 吞异常\n5. 修改后说明根因和修复思路\n6. 最多修改 max_files 个文件",
  "tools": ["read", "write", "edit", "bash", "lsp", "grep"],
  "lsp": true
}
```

---

## 7. 代码智能层

### 7.1 能力矩阵

| 能力 | 工具 | 用途 |
|------|------|------|
| 增量解析 | Tree-sitter | 构建/更新 AST，符号提取 |
| 语义导航 | LSP (pyright/gopls/tsserver/rust-analyzer) | 跨文件定义跳转、引用查找 |
| 向量检索 | Embedding + Qdrant | 语义召回 |
| 关键词检索 | BM25 (Tantivy) | 精确符号/错误码匹配 |
| Repo Map | 文件树 + 符号摘要 | 全局上下文 |
| 调用图 | 自研 | 调用链 DFS |

### 7.2 索引策略

- 监听 git 事件增量更新
- 向量索引按**符号块（函数级）**而非整文件，提升召回精度
- Embedding：bge-m3 / text-embedding-3-large

### 7.3 MCP 封装（Phase 3）

将代码智能层封装为 MCP Server，通过 `opencode mcp add` 注册，让 OpenCode Build Agent 可主动检索：

```bash
opencode mcp add code-intelligence --command "python" --args "mcp_code_intel.py"
```

---

## 8. 验证护栏

### 8.1 三重护栏

1. **编译门**：补丁必须通过 build
2. **测试门**：
   - 复现用例从 FAIL → PASS（证明修复有效）
   - 原有全量测试无新增 FAIL（证明无回归）
   - 覆盖率不下降
3. **静态门**：lint / typecheck + 安全扫描（semgrep / codeql）

### 8.2 验证执行

通过 OpenCode 终端工具或 WSL2 Docker 执行：

```python
class VerificationGate:
    def verify(self, repo_path: str, patch: str, repro_case: str) -> VerifyReport:
        results = {}
        results["compile"] = self._run_cmd(repo_path, self.build_cmd)
        results["test"] = self._run_tests(repo_path, repro_case)
        results["static"] = self._run_cmd(repo_path, self.lint_cmd)
        passed = all(r["ok"] for r in results.values())
        return VerifyReport(passed=passed, details=results)
```

### 8.3 失败处理

- 失败 → 收集失败证据 → 反馈 FixExecutor.iterate()（最多 3 轮）
- 超过 3 轮 → 转 `NEEDS_REVIEW` 并附带失败 trace

---

## 9. Win11 部署方案

### 9.1 组件部署

| 组件 | Win11 方案 | 说明 |
|------|-----------|------|
| OpenCode CLI | `scoop install opencode` | 原生 Windows 支持 |
| 编排层 | Python (LangGraph) 跑 Win11 本地 | subprocess 调用 opencode |
| 沙箱执行 | WSL2 + Docker Desktop | 完整 Linux 沙箱 |
| LSP Server | 各语言 LSP | OpenCode 自动加载 |
| 向量库 | Qdrant (Docker in WSL2) | 轻量部署 |
| 代码检出 | Win11 原生 git 或 WSL2 内 | OpenCode 在项目目录工作 |

### 9.2 关键配置

```bash
# 1. 安装 opencode
scoop install opencode

# 2. 配置公司订阅认证
opencode auth login

# 3. 启用 LSP 工具
export OPENCODE_EXPERIMENTAL_LSP_TOOL=1

# 4. 启动常驻后端
opencode serve --port 4096

# 5. 验证
opencode run --format json "ping"
```

### 9.3 文件系统注意

- 代码检出在 WSL2 内（`\\wsl$\...`）或 Win11 本地，**避免跨文件系统读写**（性能损耗大）
- Docker 卷挂载用 WSL2 内路径

---

## 10. 演进路线

### Phase 1（0-1 月）— OpenCode 集成快速闭环

- LangGraph 状态机骨架 + WSL2 沙箱
- 问题理解 + 向量/BM25 检索 + Tree-sitter AST 定位
- OpenCodeExecutor + 自定义 issue-fixer agent
- 编译门 + 测试门
- **目标**：单语言（Python 或 TS），简单 Bug 端到端跑通

### Phase 2（1-3 月）— 执行层抽象 + 定位增强

- 定义 FixExecutor 接口，OpenCode 作为默认实现
- DiffAuditor diff 审计 + 禁用模式检测
- 接入 LSP 语义导航 + 调用图分析
- 多语言支持（Go / Java）
- 多候选补丁 + 自评选优
- 静态门 + 安全扫描
- Langfuse 可观测接入

### Phase 3（3-6 月）— 核心资产沉淀

- 代码智能层封装为 MCP Server，反向注入 OpenCode
- 记忆与知识库 RAG，历史相似 issue 复用
- 修复模式自学习：成功补丁反哺知识库
- NativeExecutor 自研执行器（可选，作为并跑/备份）
- Human-in-the-Loop 审核闭环优化

**切换保险**：得益于 Phase 2 抽象层，若 OpenCode 出现输出不稳定/配额不足/定制受限，可切换或并跑自研执行器，迁移成本限于接口层。

---

## 11. Phase 1 周粒度任务拆解

### Week 1 — 环境与骨架

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| Win11 环境搭建 | opencode + WSL2 + Docker 就绪 | `opencode run --format json "ping"` 返回 JSON |
| 公司订阅认证 | auth.json 配置完成 | `opencode auth list` 显示已认证 |
| LangGraph 状态机骨架 | 6 阶段状态枚举 + 空节点 | 状态机可跑空流程不报错 |
| issue-fixer agent 配置 | opencode.json 就绪 | `opencode agent list` 含 issue-fixer |

### Week 2 — 定位 MVP

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 问题理解节点 | IssueProfile schema + LLM 调用 | 输入示例 issue → 输出合法 IssueProfile |
| 向量检索 | Qdrant + bge-m3 + 索引脚本 | 给定查询召回 Top-10 文件 |
| BM25 检索 | Tantivy 索引 + RRF 融合 | 召回结果含目标文件 |
| Tree-sitter AST | 符号提取 + 文件→符号映射 | 能从文件定位到函数级 |

### Week 3 — 修复闭环

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| OpenCodeExecutor | execute() + _parse() | 调用 opencode run 返回 FixResult |
| opencode serve 常驻 | 启动脚本 + attach 验证 | --session 多轮迭代可用 |
| 方案编排节点 | 修复假设 + PatchPolicy 组装 | 传入 FixExecutor 的 request 合法 |
| 端到端联调 | 单个真实 Bug 跑通 | 定位→修复→验证 全链路无断点 |

### Week 4 — 验证与收尾

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 编译门 | build 命令执行 + 结果判定 | 补丁不可 build 时门拦截 |
| 测试门 | 测试执行 + FAIL→PASS 判定 | 复现用例转 PASS 才放行 |
| DiffAuditor | 文件白名单 + 禁用模式检测 | 越界/禁用 diff 被拦截 |
| PR 生成 | 根因/方案/证据/测试描述 | PR 描述含完整证据链 |
| 端到端验收 | 3 个真实 Bug 跑通 | ≥1 个无人工修改即合并 |

---

## 12. 评估与度量

### 12.1 离线评估

- SWE-bench / SWE-bench-Verified resolve rate
- 自建内部 issue 集（标注根因 + 黄金补丁）

### 12.2 在线指标

| 指标 | 目标 |
|------|------|
| 定位准确率（命中根因文件/符号/行） | ≥70% |
| 一次通过率（无需人工修改即合并） | ≥40% |
| 平均迭代轮数 | ≤2 |
| 端到端耗时 | <15 min/issue |
| token 成本/issue | < $2 |
| 人工审核通过率 | ≥60% |
| 回归率 | <1% |

### 12.3 质量红线

- 回归率 < 1%
- 禁用 diff 模式触发率 = 0
- 高风险变更 100% 人工审核

---

## 13. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| OpenCode headless 输出格式不稳定 | 高 | `--format json` + schema 校验 + fallback 解析 |
| 执行层黑盒，diff 超范围 | 高 | DiffAuditor 文件白名单审计 + 禁用模式硬阻断 |
| Win11/WSL2 跨界性能 | 中 | 代码检出在 WSL2 内，避免跨文件系统读写 |
| 公司订阅配额限制 | 中 | 小模型初筛/定位，大模型仅补丁生成；serve 复用减冷启动 |
| opencode 版本演进破坏兼容 | 中 | FixExecutor 抽象层隔离；锁定版本 + CI 回归 |
| 定位幻觉 | 高 | 三重护栏 + 证据强制 + 置信度阈值 |
| 验证循环死锁 | 中 | K=3 轮上限 + 转人工 |
| LSP 加载延迟 | 低 | opencode serve 常驻，避免冷启动 |

---

## 附录：技术选型总表

| 层 | 选型 | 理由 |
|----|------|------|
| Agent 编排 | LangGraph | 显式状态图、checkpoint、human-in-the-loop |
| LLM 主力 | Claude Sonnet 4 / GPT-4.1 | SWE-bench 领先 |
| 代码解析 | Tree-sitter | 多语言、增量 |
| 语义导航 | OpenCode 内置 LSP | 复用成熟基建 |
| 向量库 | Qdrant | 高性能、支持过滤 |
| Embedding | bge-m3 | 多语言、代码语义好 |
| 沙箱 | WSL2 + Docker | Win11 成熟方案 |
| 执行层 | OpenCode headless | 复用 Build/LSP/Git |
| 补丁审计 | 自研 DiffAuditor | 文件白名单 + 禁用模式 |
| 可观测 | Langfuse | trace、成本、质量分析 |
| 任务队列 | Celery / Temporal | 长任务编排、可重试 |

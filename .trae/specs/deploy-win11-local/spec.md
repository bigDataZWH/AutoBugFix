# Win11 本地部署 Spec

## Why
硬约束要求平台在 Windows 11 本地可部署、可调试，复用 opencode 订阅大模型，不重复造轮子。需以最小依赖在 Win11 上完成整套流水线本地化运行，支持联机与离线两种模式，并提供能力降级矩阵（联机全量/离线轻量/纯 Mock 演示），确保从最低配置到推荐配置的不同环境均可演示与验证，并为后续生产可用化奠定基线。

## What Changes
- 新增 Win11 本地部署环境要求矩阵（OS/Python/内存/磁盘/网络最低与推荐配置），WSL2 可选启用但不强制。
- 新增一键启动脚本（`start.ps1` / `start.sh`），覆盖完整流程：克隆仓库→进入后端目录→创建虚拟环境→激活→安装依赖→启动服务→打开交互台。
- 新增联机与离线双模式：联机模式对接 opencode 订阅 LLM；离线模式预置模型与样例库，无网络亦可用。
- **BREAKING** 新增能力降级矩阵三档开关 `RCA_RUNTIME_MODE`，影响 CodeGraph/LightRAG 双图谱开关与兜底检索策略：联机全量（双图谱交叉验证）/ 离线轻量（仅 BM25 检索 + 调用图缓存）/ 纯 Mock 演示（双图谱关闭 + `mock_data.py` 预置样例）。切换模式需重启服务。
- 新增交互台访问入口 `rca-command.html`，经 `http://localhost:8000/rca-command.html` 访问，渲染 6 段流水线 + 4 Tab 报告（根因/知识库/最佳实践/方案）。
- 新增 RCA 分析 HTTP 接口：`POST /api/v1/rca/analyze` 提交分析任务返回 `task_id`，`SSE GET /api/v1/rca/{task_id}/stream` 流式推送 6 阶段进度与产物。
- 新增 WSL2 可选启用路径（不强制），通过 `start.sh` 在 WSL/Git Bash 下等价运行。
- 新增本地依赖清单 `requirements.txt`，含 `lightrag-hku`、`FastAPI`、`LangGraph`、`Celery`、`Redis`、`lightrag`、CodeGraph 等依赖。
- 新增里程碑验收指标体系：M1 原型验证（端到端 <30s）、M2 引擎接入（Top-3 ≥75%）、M3 双图谱融合（误报率下降 ≥40%）、M4 生产可用（P95 <12s，可用性 99.5%）。

## Impact
- Affected specs: 全部模块（部署承载所有能力）；`orchestrate-five-agent-engine`（Celery/Redis/Postgres 本地运行与任务编排）；`setup-lightrag-retrieval-engine`（Postgres 一体化本地 + LightRAG 本地向量库）；`build-codegraph-knowledge-graph`（CodeGraph Win `install.ps1` 与图谱构建峰值约 3.2GB 内存约束）；`build-dual-gate-flywheel`（SSE 推送与 HIL 确认面板在交互台呈现）。
- Affected code: `start.ps1` / `start.sh` 启动脚本；`requirements.txt`；`mock_data.py`；`rca-command.html` 交互台；`RCA_RUNTIME_MODE` 降级模式开关；RCA 路由层 `/api/v1/rca/*`；降级检索策略分支（BM25 兜底、调用图缓存）；配置加载模块（环境矩阵读取）。

## ADDED Requirements

### Requirement: 环境要求矩阵
The system SHALL 在 Win11 上以最小依赖运行，并对外暴露环境要求矩阵，覆盖 OS（Win11 22H2 最低 / 23H2+ 推荐，WSL2 可选）、Python（3.10 最低 / 3.11 推荐，conda 隔离）、内存（8GB 最低 / 16GB 推荐，CodeGraph 构建峰值约 3.2GB）、磁盘（2GB 最低 / 5GB SSD 推荐，向量库与图谱缓存占用）、网络（可选，首次联机推荐）。

#### Scenario: 最低配置可跑 Mock 演示
- **WHEN** Win11 22H2 + Python 3.10 + 8GB 内存 + 离线环境
- **THEN** 平台以纯 Mock 模式启动，`mock_data.py` 预置样例可用，6 段流水线渲染正常，交互台可访问

#### Scenario: 推荐配置联机全量
- **WHEN** Win11 23H2+ + Python 3.11 + 16GB 内存 + 联机 + 5GB SSD
- **THEN** 双图谱交叉验证完整运行，CodeGraph 构建峰值约 3.2GB 内存可承受，无 OOM

#### Scenario: 内存不足降级
- **WHEN** 可用内存 <8GB 且强制启用 CodeGraph
- **THEN** 启动前置检查告警，建议切换 `RCA_RUNTIME_MODE=offline_light`，仍可完成演示

#### Scenario: Python 版本不满足
- **WHEN** Python 版本 <3.10
- **THEN** 启动脚本前置校验失败，输出明确错误并提示安装 3.10+，不进入依赖安装阶段

#### Scenario: 磁盘空间不足
- **WHEN** 剩余磁盘 <2GB
- **THEN** 启动前置检查告警并中止，提示需 ≥2GB（推荐 5GB SSD）

### Requirement: 一键启动脚本
The system SHALL 提供一键启动脚本（Win `start.ps1` / WSL-GitBash `start.sh`），执行：克隆仓库 `https://github.com/bigDataZWH/AutoBugFix.git` → 进入 `rca-backend` 目录 → `python -m venv .venv` → `.venv\Scripts\activate` → `pip install -r requirements.txt` → 启动服务 → 访问 `http://localhost:8000/rca-command.html`。

#### Scenario: Windows 一键启动
- **WHEN** 执行 `.\start.ps1`
- **THEN** 自动创建 `.venv`、安装 `requirements.txt`、启动服务，交互台可访问 `http://localhost:8000/rca-command.html`

#### Scenario: WSL/Git Bash 一键启动
- **WHEN** 在 WSL2 或 Git Bash 中执行 `bash start.sh`
- **THEN** 执行等价流程（`source .venv/bin/activate`），交互台可访问同一 URL

#### Scenario: 仓库已克隆增量启动
- **WHEN** 本地已存在 `AutoBugFix` 目录
- **THEN** 脚本跳过 `git clone`，直接进入目录并复用 `.venv`（若存在则跳过创建），仅执行 `pip install` 与启动

#### Scenario: 依赖安装失败兜底
- **WHEN** `pip install` 因网络或版本冲突失败
- **THEN** 脚本输出错误码与失败包名，提示离线模式可改用预置 wheel，不继续启动

#### Scenario: 端口占用
- **WHEN** `8000` 端口已被占用
- **THEN** 启动脚本检测后提示占用进程，提供 `--port` 备选参数或建议终止占用进程

### Requirement: 联机与离线双模式
The system SHALL 支持联机与离线两种模式，由 `RCA_RUNTIME_MODE` 控制。联机模式对接 opencode 订阅 LLM；离线模式预置模型与样例库，无网络亦可用，并自动降级检索策略。

#### Scenario: 联机全量
- **WHEN** 联机且 `RCA_RUNTIME_MODE=online_full`
- **THEN** 双图谱（CodeGraph + LightRAG）完整运行，调用 opencode 订阅 LLM，启用双图谱交叉验证

#### Scenario: 离线轻量降级
- **WHEN** 无网络环境且 `RCA_RUNTIME_MODE=offline_light`
- **THEN** 预置模型与样例库可用，平台以 BM25 检索 + 调用图缓存兜底完成演示

#### Scenario: 网络中断自动降级
- **WHEN** 联机运行中 LLM 调用超时或网络中断
- **THEN** 自动降级为本地小模型兜底 + 请求合并，记录降级事件，不中断流水线

#### Scenario: 首次联机推荐
- **WHEN** 首次启动且无本地缓存
- **THEN** 引导联机以拉取模型与依赖，完成后可切换离线模式运行

### Requirement: 能力降级矩阵
The system SHALL 提供三档能力降级矩阵：联机全量（CodeGraph 完整 + LightRAG 完整 + 双图谱交叉验证）、离线轻量（CodeGraph 降级 + LightRAG 降级 + 仅 BM25 检索 + 调用图缓存）、纯 Mock 演示（CodeGraph/LightRAG 关闭 + `mock_data.py` 预置样例）。

#### Scenario: 联机全量双图谱交叉验证
- **WHEN** 联机且资源充足（≥16GB）
- **THEN** 双图谱交叉验证完整运行，Top-3 命中率 ≥95%，误报率（双闸门后）≤5%

#### Scenario: 离线轻量 BM25 兜底
- **WHEN** 离线环境
- **THEN** 降级为 BM25 检索 + 调用图缓存，仍可输出根因候选，但召回质量受限

#### Scenario: 纯 Mock 演示
- **WHEN** 无模型 / 无数据 / `RCA_RUNTIME_MODE=mock_demo`
- **THEN** 以 `mock_data.py` 预置样例完成 6 段流水线渲染，用于 demo 与验收

#### Scenario: 模式切换需重启
- **WHEN** 运行中切换 `RCA_RUNTIME_MODE`
- **THEN** 提示需重启服务生效，不热切换以避免图谱状态不一致

### Requirement: 交互台访问入口与 SSE 推送
The system SHALL 提供 `rca-command.html` 交互台，经 `http://localhost:8000/rca-command.html` 访问，支持 SSE 流式阶段推送与 HIL 人工确认面板。

#### Scenario: 交互台 4 Tab 报告渲染
- **WHEN** 服务启动完成并访问交互台
- **THEN** 渲染 6 段流水线 + 4 Tab 报告（根因/知识库/最佳实践/方案），数据来自 SSE 产物

#### Scenario: SSE 流式阶段推送
- **WHEN** 提交分析任务获得 `task_id` 后订阅 SSE
- **THEN** `GET /api/v1/rca/{task_id}/stream` 依次推送 6 阶段（症状确认/链路分析/代码定位/根因确认/修复方案/HIL）进度与产物 JSON

#### Scenario: HIL 确认面板
- **WHEN** 流水线推进至根因确认阶段
- **THEN** 交互台弹出 HIL 面板，等待人工确认 Top-3 根因，确认后继续下一阶段

#### Scenario: SSE 断线重连
- **WHEN** SSE 连接中断
- **THEN** 客户端基于 `task_id` 重连并从最后 `last-event-id` 续传，不丢阶段产物

#### Scenario: 交互台无服务响应
- **WHEN** 服务未启动访问交互台
- **THEN** 浏览器返回连接失败，交互台加载脚本提示用户先执行启动脚本

### Requirement: 部署验收与里程碑指标
The system SHALL 在本地部署完成后满足四阶段里程碑验收指标：M1（6 阶段全跑通，端到端 <30s）、M2（Top-3 命中率 ≥75%）、M3（误报率下降 ≥40%）、M4（P95 <12s，可用性 99.5%）；核心评估指标 Top-1 ≥80%、Top-3 ≥95%、P95 ≤12s、误报率（双闸门后）≤5%。

#### Scenario: M1 原型验证（第 1-2 周）
- **WHEN** 流水线骨架 + Mock 数据就绪
- **THEN** 6 阶段全跑通，端到端 <30s

#### Scenario: M2 引擎接入（第 3-5 周）
- **WHEN** CodeGraph + LightRAG 接入
- **THEN** Top-3 命中率 ≥75%

#### Scenario: M3 双图谱融合（第 6-8 周）
- **WHEN** 交叉验证 + 知识飞轮上线
- **THEN** 误报率相对 M2 下降 ≥40%

#### Scenario: M4 生产可用（第 9-12 周）
- **WHEN** 双闸门 + 监控面板就绪
- **THEN** P95 响应 <12s，可用性 ≥99.5%

#### Scenario: 风险兜底（CodeGraph 构建超时）
- **WHEN** 大仓库 CodeGraph 构建超时（中概率风险）
- **THEN** 触发增量构建 + 缓存预热，记录超时事件，不阻断部署

### Requirement: 风险与应对
The system SHALL 对三类部署与运行风险提供兜底应对：大仓库 CodeGraph 构建超时（中概率，增量构建 + 缓存预热）、LLM 调用配额受限（中概率，本地小模型兜底 + 请求合并）、跨语言调用链断裂（高概率，桥接节点 + 人工标注回流）。

#### Scenario: CodeGraph 构建超时兜底
- **WHEN** 单仓库构建超过预设阈值
- **THEN** 切换增量构建 + 缓存预热，返回部分图谱并标记 incomplete

#### Scenario: LLM 配额受限兜底
- **WHEN** opencode 订阅 LLM 配额耗尽或限流
- **THEN** 自动切换本地小模型兜底 + 合并多请求批量推理，记录降级

#### Scenario: 跨语言调用链断裂兜底
- **WHEN** 跨语言调用图出现断点（高概率）
- **THEN** 插入桥接节点 + 触发人工标注回流任务，不中断根因定位流水线

## 技术细节

### 接口定义

交互台与启动流程接口：

```text
交互台入口:    http://localhost:8000/rca-command.html
仓库地址:      https://github.com/bigDataZWH/AutoBugFix.git
后端目录:      AutoBugFix/rca-backend
虚拟环境:      .venv (Win: .venv\Scripts\activate; WSL/Bash: source .venv/bin/activate)
```

RCA 分析交互接口：

```http
POST /api/v1/rca/analyze
Content-Type: application/json
Request Body:
{
  "bug_id": "string",
  "symptom": "string",
  "error_type": "string|null",
  "suspect_service": "string|null",
  "runtime_mode": "online_full|offline_light|mock_demo"
}
Response 202:
{
  "task_id": "string (uuid)",
  "status": "queued"
}
```

SSE 流式推送接口：

```http
GET /api/v1/rca/{task_id}/stream
Accept: text/event-stream
Response (SSE):
event: stage
data: {"stage": 1, "name": "症状确认", "status": "running", "artifact": {...}}
event: stage
data: {"stage": 4, "name": "根因确认", "status": "hil_wait", "top3": [...]}
event: done
data: {"task_id": "...", "total_elapsed_ms": 12345}
```

HIL 确认回调接口：

```http
POST /api/v1/rca/{task_id}/confirm
Content-Type: application/json
Request Body:
{
  "confirmed_root_cause_id": "string",
  "operator": "string",
  "comment": "string|null"
}
Response 200:
{ "status": "confirmed", "next_stage": "修复方案" }
```

### 数据结构

环境要求矩阵：

```yaml
environment_matrix:
  os:
    minimum: "Win11 22H2"
    recommended: "Win11 23H2+"
    note: "WSL2 可选启用，不强制"
  python:
    minimum: "3.10"
    recommended: "3.11"
    note: "conda 环境隔离"
  memory:
    minimum: "8GB"
    recommended: "16GB"
    note: "CodeGraph 构建峰值约 3.2GB"
  disk:
    minimum: "2GB"
    recommended: "5GB SSD"
    note: "向量库与图谱缓存占用"
  network:
    minimum: "可选"
    recommended: "首次联机"
    note: "离线模式预置模型与样例库"
```

能力降级矩阵：

```yaml
degradation_matrix:
  online_full:
    codegraph: "完整"
    lightrag: "完整"
    fallback: "双图谱交叉验证"
    runtime_mode: "online_full"
    env_required: "recommended"
  offline_light:
    codegraph: "降级"
    lightrag: "降级"
    fallback: "仅 BM25 检索 + 调用图缓存"
    runtime_mode: "offline_light"
    env_required: "minimum"
  mock_demo:
    codegraph: "关闭"
    lightrag: "关闭"
    fallback: "mock_data.py 预置样例"
    runtime_mode: "mock_demo"
    env_required: "minimum"
```

里程碑与核心指标矩阵：

```yaml
milestones:
  M1_原型验证:
    week: "第 1-2 周"
    deliverable: "流水线骨架 + Mock 数据"
    kpi: "6 阶段全跑通，端到端 <30s"
  M2_引擎接入:
    week: "第 3-5 周"
    deliverable: "CodeGraph + LightRAG"
    kpi: "Top-3 命中率 ≥75%"
  M3_双图谱融合:
    week: "第 6-8 周"
    deliverable: "交叉验证 + 知识飞轮"
    kpi: "误报率下降 ≥40%"
  M4_生产可用:
    week: "第 9-12 周"
    deliverable: "双闸门 + 监控面板"
    kpi: "P95 <12s，可用性 99.5%"

core_metrics:
  top1_hit_rate: "≥80%"
  top3_coverage: "≥95%"
  p95_latency: "≤12s"
  false_positive_after_dual_gate: "≤5%"

risks:
  - name: "大仓库 CodeGraph 构建超时"
    probability: "中"
    impact: "首次构建慢"
    mitigation: "增量构建 + 缓存预热"
  - name: "LLM 调用配额受限"
    probability: "中"
    impact: "响应延迟"
    mitigation: "本地小模型兜底 + 请求合并"
  - name: "跨语言调用链断裂"
    probability: "高"
    impact: "定位偏差"
    mitigation: "桥接节点 + 人工标注回流"
```

### 配置项

`requirements.txt` 关键依赖：

```text
fastapi
uvicorn[standard]
langgraph
celery
redis
lightrag-hku
lightrag
# CodeGraph 依赖（构建调用图与跨语言解析）
# 向量库与图谱缓存依赖
```

环境变量与启动配置：

```bash
RCA_RUNTIME_MODE=online_full|offline_light|mock_demo
RCA_HOST=127.0.0.1
RCA_PORT=8000
RCA_INTERACTIVE_CONSOLE_PATH=/rca-command.html
RCA_CODEGRAPH_BUILD_PEAK_MEM_GB=3.2
RCA_OFFLINE_MODEL_DIR=./offline_models
RCA_MOCK_DATA_PATH=./mock_data.py
```

`start.ps1`（Windows 一键启动）：

```powershell
# 1. 克隆并进入后端目录
git clone https://github.com/bigDataZWH/AutoBugFix.git
cd AutoBugFix/rca-backend

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. 一键启动（Windows）
.\start.ps1

# 访问交互台
start http://localhost:8000/rca-command.html
```

`start.sh`（WSL / Git Bash 等价启动）：

```bash
# 1. 克隆并进入后端目录
git clone https://github.com/bigDataZWH/AutoBugFix.git
cd AutoBugFix/rca-backend

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 一键启动（WSL / Git Bash）
bash start.sh

# 访问交互台
xdg-open http://localhost:8000/rca-command.html || start http://localhost:8000/rca-command.html
```

## 验收指标
- M1 原型验证：6 阶段全跑通，端到端延迟 <30s（目标值，第 1-2 周达成）。
- M2 引擎接入：CodeGraph + LightRAG 接入后，Top-3 命中率 ≥75%（目标值，第 3-5 周达成）。
- M3 双图谱融合：交叉验证 + 知识飞轮上线后，误报率相对 M2 下降 ≥40%（目标值，第 6-8 周达成）。
- M4 生产可用：P95 端到端响应 <12s，可用性 ≥99.5%（目标值，第 9-12 周达成）。
- 核心评估指标：Top-1 根因命中率 ≥80%；Top-3 根因覆盖率 ≥95%；P95 端到端响应 ≤12s；误报率（双闸门后）≤5%。
- 内存约束：CodeGraph 构建阶段峰值内存约 3.2GB，在 8GB 最低配置下可完成 Mock 演示，在 16GB 推荐配置下可完成联机全量构建。
- 交互台可用性：`http://localhost:8000/rca-command.html` 启动后 5s 内可访问，4 Tab 报告与 HIL 面板渲染完整。

## UT 测试方案

测试框架约定：Python `pytest` + `pytest-asyncio` + `pytest-cov`；部署脚本 `start.ps1` 经 `PSScriptAnalyzer` 校验、`start.sh` 经 `shellcheck` + `bash -n` 校验；降级矩阵经环境变量 `RCA_RUNTIME_MODE`（`online_full`/`offline_light`/`mock_demo`，对应简称 online/offline/mock）切换；环境隔离使用 `tmp_path` fixture 与临时 venv。目标覆盖率 line + branch ≥80%。每个用例包含：用例名、被测组件、输入、预期输出、mock 策略。

### 用例 1: test_env_validation
- **被测组件**: 启动前置检查模块（`env_check.py`，`start.ps1`/`start.sh` 内置校验段）
- **输入**: 构造环境快照四组组合（Python 3.9/3.10/3.11、Node 18/20、Postgres 14/15、Redis 6/7）
- **预期输出**: Python 3.11+ / Node 20+ / Postgres 15+ / Redis 7+ 校验通过；低于阈值抛 `EnvValidationError`，输出明确版本提示，退出码非 0
- **mock 策略**: `monkeypatch` 替换 `sys.version_info` 与 `subprocess.run`（模拟 `node --version`/`psql --version`/`redis-server --version` 输出）；不真实安装运行时

### 用例 2: test_requirements_install
- **被测组件**: 依赖安装流程（`pip install -r requirements.txt` 干跑）
- **输入**: `requirements.txt` 解析后的依赖清单
- **预期输出**: `pip install --dry-run` 退出码 0，`pip check` 无依赖冲突告警，版本解析树可生成
- **mock 策略**: 临时 venv 执行 `pip install --dry-run`；mock pypi 索引指向本地 wheelhouse；断言无 `ConflictWarning`

### 用例 3: test_start_ps1_script
- **被测组件**: `start.ps1` 启动脚本
- **输入**: `start.ps1` 源码 + 模拟环境（仓库已克隆/未克隆两种）
- **预期输出**: `PSScriptAnalyzer` 无 Error 级诊断；服务启动顺序为 venv→pip→服务→浏览器；`Test-NetConnection` 断言 8000 端口监听
- **mock 策略**: `Invoke-ScriptAnalyzer` 静态分析；mock `git clone`/`pip install`/`Start-Process` 为 no-op；端口检查 mock 返回监听状态

### 用例 4: test_start_sh_script
- **被测组件**: `start.sh` 启动脚本
- **输入**: `start.sh` 源码 + 模拟环境
- **预期输出**: `shellcheck -S error` 无错误，`bash -n` 语法校验通过；启动顺序 venv→pip→服务→浏览器；`ss`/`lsof` 断言 8000 端口监听
- **mock 策略**: `shellcheck` + `bash -n` 静态分析；mock `git`/`pip`/`xdg-open`；断言启动顺序与端口

### 用例 5: test_degradation_matrix_online
- **被测组件**: 降级矩阵调度器（`runtime_mode.py`）
- **输入**: `RCA_RUNTIME_MODE=online_full`
- **预期输出**: CodeGraph/LightRAG 双图谱启用、双图谱交叉验证开关 ON、检索策略为向量检索 + 交叉验证
- **mock 策略**: mock LLM 客户端返回固定 embedding；mock CodeGraph/LightRAG 构建入口返回成功标志；仅断言组件启用状态，不验证真实图谱内容

### 用例 6: test_degradation_matrix_offline
- **被测组件**: 降级矩阵调度器（`runtime_mode.py`）
- **输入**: `RCA_RUNTIME_MODE=offline_light`
- **预期输出**: LightRAG 降级/禁用、检索回退 BM25 + ripgrep、调用图缓存启用
- **mock 策略**: mock `lightrag` 模块抛 `UnavailableError` 触发降级；mock ripgrep 子进程返回命中行；断言降级日志与 fallback 路径

### 用例 7: test_degradation_matrix_mock
- **被测组件**: 降级矩阵调度器（`runtime_mode.py`）
- **输入**: `RCA_RUNTIME_MODE=mock_demo`
- **预期输出**: LLM/Trace/CodeGraph 全 Mock，从 `mock_data.py` 加载预置样例；6 段流水线均返回 mock 产物
- **mock 策略**: mock 全部外部依赖；断言 `mock_data.py` 样例被加载、真实调用计数 =0、mock 调用计数 >0

### 用例 8: test_mode_switch
- **被测组件**: 运行时模式切换逻辑（`runtime_mode.py` 热加载守卫）
- **输入**: 运行中修改 `RCA_RUNTIME_MODE`（online_full → offline_light）
- **预期输出**: 当前会话不热切换，返回"需重启生效"提示；重启后新模式生效
- **mock 策略**: mock 配置热加载触发器；断言当前模式不变 + 提示消息命中；模拟重启后断言新模式生效

### 用例 9: test_frontend_page_load
- **被测组件**: `rca-command.html` 单页前端
- **输入**: `rca-command.html` 源码文本
- **预期输出**: HTML 自包含、无 `<script src="http*">`/`<link href="http*">` 外部 CDN 依赖、内联 JS/CSS 可被 DOM 解析；包含 6 段流水线节点与 4 Tab 容器
- **mock 策略**: 用 `beautifulsoup4` 解析 DOM；正则断言无外链；不启动真实 HTTP 服务

### 用例 10: test_api_health_check
- **被测组件**: `GET /api/v1/health` 健康检查接口
- **输入**: HTTP GET 请求
- **预期输出**: 200，body 含各组件状态（`postgres`/`redis`/`lightrag`/`codegraph`/`llm`），状态值 `up|down|degraded`
- **mock 策略**: `httpx.AsyncClient` + `ASGITransport` 直连 ASGI app；mock 各组件健康探针返回 up/down；断言聚合状态正确

### 用例 11: test_sse_endpoint
- **被测组件**: `POST /api/v1/rca/analyze` + `GET /api/v1/rca/{task_id}/stream`
- **输入**: 提交 bug 单 JSON → 获取 `task_id` → 订阅 SSE 流
- **预期输出**: analyze 返回 202 + `task_id`；stream 响应 `Content-Type: text/event-stream`；依次收到 6 个 `event: stage` + 末尾 `event: done`（携带 `total_elapsed_ms`）
- **mock 策略**: `pytest-asyncio` 异步驱动；mock 流水线各阶段执行器即时完成；用 `httpx-sse` 解析事件流断言顺序与 done 字段

### 用例 12: test_port_conflict
- **被测组件**: 端口冲突检测（`port_check.py`）
- **输入**: 8000 / 6379 / 5432 端口被模拟占用
- **预期输出**: 抛 `PortConflictError`，错误消息含占用端口、PID 与 `--port` 备选建议；退出码非 0
- **mock 策略**: mock `socket.bind` 抛 `OSError` / `psutil.net_connections` 返回占用记录；断言优雅报错而非崩溃栈

### 用例 13: test_log_rotation
- **被测组件**: 日志轮转（`logging` `RotatingFileHandler` 配置）
- **输入**: 写入超过 `RCA_LOG_MAX_BYTES` 阈值（如 1MB）的日志
- **预期输出**: 自动生成 `.1`/`.2` 滚动文件，主日志文件截断至阈值内，保留 `RCA_LOG_BACKUP_COUNT` 个备份
- **mock 策略**: `tmp_path` fixture 隔离日志目录；mock 时间避免延迟；断言文件数量与单文件大小不超阈值

### 用例 14: test_config_override
- **被测组件**: 配置加载器（`config.py`）
- **输入**: 同一配置项在「默认值 / `.env` 文件 / 环境变量」三层分别设值
- **预期输出**: 优先级 环境变量 > `.env` > 默认值；类型转换正确（int/bool/str）
- **mock 策略**: `monkeypatch.setenv` 注入环境变量；`tmp_path` 写入 `.env`；断言三层覆盖解析结果

## E2E 测试方案

测试框架约定：`docker-compose` 拉起完整环境（Postgres 15 + Redis 7 + app 服务）+ `pytest` + `pytest-asyncio`；每场景独立设置 `RCA_RUNTIME_MODE`；用 `httpx` + `httpx-sse` 驱动 HTTP/SSE，headless 浏览器驱动前端集成。每个场景包含：场景名、前置条件、测试步骤、预期结果、断言点。

### 场景 1: e2e_full_local_startup
- **前置条件**: Win11 / Linux 宿主，docker-compose 就绪，仓库已克隆
- **测试步骤**: ① 执行 `start.ps1`（或 `start.sh`）② 轮询 `http://localhost:8000/rca-command.html` 直到 200 ③ 调用 `GET /api/v1/health` ④ 记录启动到 health=up 的耗时
- **预期结果**: 全部服务启动，交互台 5s 内可访问，健康检查通过
- **断言点**: HTTP 200；health 各组件 `up`；启动耗时 <30s（M1）

### 场景 2: e2e_rca_analyze_request
- **前置条件**: 服务已启动，`RCA_RUNTIME_MODE=mock_demo`（冒烟）或 `online_full`
- **测试步骤**: ① 前端提交 Bug 单 ② `POST /api/v1/rca/analyze` 获取 `task_id` ③ 订阅 `GET /api/v1/rca/{task_id}/stream` 收集事件 ④ 解析末尾方案产物
- **预期结果**: analyze 返回 202 + `task_id`；SSE 依次推送 6 阶段；末尾 `done` 携带耗时与方案 JSON
- **断言点**: `task_id` 为 uuid；收到 6 个 stage 事件且顺序正确；`done` 含 `total_elapsed_ms`；方案产物非空

### 场景 3: e2e_online_mode_full
- **前置条件**: 推荐配置（Python 3.11 / 16GB），网络可用，opencode 订阅 LLM 可达
- **测试步骤**: ① `RCA_RUNTIME_MODE=online_full` 启动 ② 提交标注样例 Bug 单 ③ 等待 SSE 完成 ④ 收集 Top-3 根因
- **预期结果**: CodeGraph + LightRAG 双图谱真实启用，交叉验证运行，产出 Top-3
- **断言点**: CodeGraph/LightRAG 组件状态 `up`；Top-3 列表长度 =3；与标注根因对比统计命中率

### 场景 4: e2e_offline_mode_degraded
- **前置条件**: `RCA_RUNTIME_MODE=offline_light`，断网（mock LLM 网络不可达）
- **测试步骤**: ① 启动服务 ② 提交 Bug 单 ③ 验证 LightRAG 降级标记 ④ 确认 ripgrep 兜底检索日志 ⑤ 收集 Top-3
- **预期结果**: LightRAG 禁用 → ripgrep/BM25 兜底，仍产出 Top-3，流水线不中断
- **断言点**: LightRAG 状态 `degraded`/`down`；日志含 ripgrep fallback 记录；Top-3 非空；无未捕获异常

### 场景 5: e2e_mock_mode_smoke
- **前置条件**: `RCA_RUNTIME_MODE=mock_demo`，无需 LLM/网络
- **测试步骤**: ① 启动服务 ② 提交 Bug 单 ③ 走完 SSE 全流程 ④ 验证降级路径
- **预期结果**: 全 Mock，6 段流水线跑通，返回 `mock_data.py` 预置样例产物
- **断言点**: LLM/Trace/CodeGraph 真实调用计数 =0；6 阶段全部 `done`；产物来源 `mock_data`

### 场景 6: e2e_milestone_kpi
- **前置条件**: 各里程碑对应数据集与标注就绪
- **测试步骤**: ① M1：测启动耗时 ② M2：统计 Top-3 命中率 ③ M3：对比双图谱前后误报率 ④ M4：压测采集 P95 与可用性
- **预期结果**: M1 <30s；M2 Top-3 ≥75%；M3 误报率下降 ≥40%；M4 P95 <12s + 可用性 99.5%
- **断言点**: 启动耗时 <30s；Top-3 命中率 ≥75%；误报率下降比例 ≥40%；P95 <12s；可用性 ≥99.5%（压测窗口内失败请求占比 ≤0.5%）

### 场景 7: e2e_restart_recovery
- **前置条件**: 分析任务进行中（SSE 推送至第 3 阶段）
- **测试步骤**: ① 记录 `last-event-id` ② 中断服务 ③ 重启 ④ 基于 `task_id` + `last-event-id` 重连 SSE ⑤ 验证断点续跑
- **预期结果**: 重启后从断点续传，不丢阶段产物，流水线继续至完成
- **断言点**: 重连后首个事件为断点之后阶段；无重复阶段；`done` 最终到达；task 状态 `interrupted`→`running`→`done`

### 场景 8: e2e_frontend_backend_integration
- **前置条件**: 服务已启动，headless 浏览器可用
- **测试步骤**: ① 打开 `rca-command.html` ② 提交 Bug 单触发 `POST /api/v1/rca/analyze` ③ 浏览器侧监听 SSE 渲染 ④ 验证 4 Tab 结果展示
- **预期结果**: 前端正确发起请求，SSE 渲染 6 阶段进度，4 Tab 报告展示最终方案
- **断言点**: DOM 含 6 段流水线节点；4 Tab 容器均有内容；SSE 阶段渲染顺序与后端一致；network 日志无外部 CDN 请求

## 跨模块集成测试方案

测试框架约定：跨模块集成测试在 `docker-compose`（Postgres 15 + Redis 7 + app 服务）环境下以 `pytest` + `pytest-asyncio` + Playwright 驱动；通过环境变量 `RCA_RUNTIME_MODE` 切换 `online_full`/`offline_light`/`mock_demo` 三级降级模式；集成范围覆盖上游 6 模块（代码中文描述、CodeGraph、LightRAG、5-Agent 引擎、双图谱、双闸门飞轮）+ 环境依赖（Python 3.11/Node 20/Postgres 15/Redis 7）+ opencode LLM 订阅，下游用户（rca-command.html、`POST /api/v1/rca/analyze`、SSE）与 CI/CD 流水线。每个场景包含：场景名、涉及模块、集成点、测试步骤、预期结果、断言点。

### 上下游依赖关系表

| 方向 | 依赖项 | 职责与集成契约 |
| --- | --- | --- |
| 上游 | 代码中文描述（模块1） | 提供代码语义切片与中文注释，供 5-Agent「链路分析」阶段引用 |
| 上游 | CodeGraph（模块2） | 构建跨语言调用图，供「代码定位」「根因确认」阶段查询 |
| 上游 | LightRAG（模块3） | 向量检索/知识库召回，供双图谱交叉验证 |
| 上游 | 5-Agent 引擎（模块4） | 编排 6 阶段流水线（症状确认→链路分析→代码定位→根因确认→修复方案→HIL） |
| 上游 | 双图谱（模块5） | CodeGraph + LightRAG 交叉验证，融合 Top-3 根因候选 |
| 上游 | 双闸门飞轮（模块6） | 闸门过滤 + 知识飞轮回流，降低误报率 |
| 上游 | 环境依赖 | Python 3.11 / Node 20 / Postgres 15 / Redis 7，运行时与数据/缓存基础设施 |
| 上游 | opencode LLM 订阅 | `online_full` 模式 LLM 推理端点（AK/SK 鉴权） |
| 下游 | rca-command.html 前端单页 | 渲染 6 段流水线 + 4 Tab 报告（根因/知识库/最佳实践/方案） |
| 下游 | POST /api/v1/rca/analyze + SSE | 用户提交分析任务、订阅阶段进度与产物 |
| 下游 | CI/CD 流水线 | pytest 全量执行 → 覆盖率报告 → 门禁阈值 → 部署 |

### 场景 integ_full_stack_startup — 全栈启动
- **涉及模块**：部署模块 + 6 上游模块 + 环境依赖
- **集成点**：`start.ps1`/`start.sh` → venv → pip → 6 模块服务 → `GET /api/v1/health`
- **测试步骤**：① 执行 `start.ps1`（Linux 下 `start.sh`）② 轮询 `http://localhost:8000/api/v1/health` 直到全 `up` ③ 记录启动到 health=up 耗时 ④ 访问 `rca-command.html` 确认可达
- **预期结果**：6 模块服务全部启动，健康检查各组件 `up`，交互台可达，启动耗时 <30s
- **断言点**：HTTP 200；`health` body 各组件 `up`；启动耗时 <30000ms（M1）；`rca-command.html` 200

### 场景 integ_frontend_to_backend_api — 前端→后端 API 全链路
- **涉及模块**：rca-command.html + 5-Agent 引擎 + SSE 推送
- **集成点**：前端 fetch → `POST /api/v1/rca/analyze` → 5-Agent 6 阶段 → `GET /api/v1/rca/{task_id}/stream`
- **测试步骤**：① Playwright 打开 `rca-command.html` ② 填入 Bug 单并提交 ③ 抓取 `POST /analyze` 响应 `task_id` ④ 前端订阅 SSE ⑤ 校验 6 阶段渲染顺序与 `done`
- **预期结果**：前端正确发起请求，SSE 依次推送 6 阶段，4 Tab 渲染最终方案
- **断言点**：`task_id` 为 uuid；收到 6 个 `event: stage` 且阶段名顺序正确；末尾 `event: done` 含 `total_elapsed_ms`；4 Tab DOM 非空；network 无外部 CDN 请求

### 场景 integ_online_full_mode — 联机全量真实集成
- **涉及模块**：CodeGraph + LightRAG + 5-Agent + 双图谱 + 双闸门飞轮 + opencode LLM
- **集成点**：`RCA_RUNTIME_MODE=online_full` → 双图谱交叉验证 → Top-3 根因
- **测试步骤**：① 以 `online_full` 启动 ② 提交标注 Bug 单 ③ 等待 SSE 完成 ④ 收集 Top-3 根因并与标注比对
- **预期结果**：CodeGraph/LightRAG 真实启用，双图谱交叉验证运行，产出 Top-3 根因候选
- **断言点**：`health` 中 CodeGraph/LightRAG 状态 `up`；Top-3 长度 =3；与标注根因命中率统计；真实 LLM 调用计数 >0

### 场景 integ_offline_light_mode — 离线轻量降级集成
- **涉及模块**：LightRAG（降级/禁用）+ ripgrep 兜底 + 5-Agent 降级路径
- **集成点**：`RCA_RUNTIME_MODE=offline_light` → LightRAG 禁用 → ripgrep/BM25 兜底检索
- **测试步骤**：① 以 `offline_light` 启动并断网 ② 提交 Bug 单 ③ 验证 LightRAG 降级标记 ④ 确认 ripgrep 兜底日志 ⑤ 收集 Top-3
- **预期结果**：LightRAG 禁用 → ripgrep/BM25 兜底，仍产出 Top-3，流水线不中断
- **断言点**：LightRAG 状态 `degraded`/`down`；日志含 ripgrep fallback 记录；Top-3 非空；无未捕获异常；真实 LLM 调用计数 =0

### 场景 integ_mock_demo_mode — Mock 冒烟集成
- **涉及模块**：LLM/Trace/CodeGraph/LightRAG 全 Mock + `mock_data.py`
- **集成点**：`RCA_RUNTIME_MODE=mock_demo` → 全部外部依赖 Mock → 流程跑通
- **测试步骤**：① 以 `mock_demo` 启动 ② 提交 Bug 单 ③ 走完 SSE 全流程 ④ 验证降级路径与产物来源
- **预期结果**：全 Mock，6 段流水线跑通，返回 `mock_data.py` 预置样例产物
- **断言点**：LLM/Trace/CodeGraph/LightRAG 真实调用计数 =0；6 阶段全部 `done`；产物来源 `mock_data`；mock 调用计数 >0

### 场景 integ_restart_recovery — 中断重启断点续跑
- **涉及模块**：Redis RCAState 持久化 + 5-Agent 阶段恢复 + SSE 断线重连
- **集成点**：Redis `RCAState` → 中断 → 重启 → 从中断 stage 继续 + `last-event-id` 续传
- **测试步骤**：① 分析任务进行至第 3 阶段 ② 记录 `last-event-id` ③ 中断服务 ④ 重启 ⑤ 基于 `task_id`+`last-event-id` 重连 SSE ⑥ 验证断点续跑
- **预期结果**：重启后从断点续传，不丢阶段产物，流水线继续至完成
- **断言点**：重连后首个事件为断点之后阶段；无重复阶段；`done` 最终到达；task 状态 `interrupted`→`running`→`done`；Redis `RCAState` 恢复命中

### 场景 integ_milestone_kpi_verification — 4 里程碑 KPI 集成验证
- **涉及模块**：部署验收 + CodeGraph + LightRAG + 双闸门 + 监控
- **集成点**：M1 启动耗时 / M2 Top-3 命中率 / M3 误报率下降 / M4 P95 + 可用性
- **测试步骤**：① M1 测启动到 health=up 耗时 ② M2 统计 Top-3 命中率 ③ M3 对比双图谱前后误报率 ④ M4 压测采集 P95 与可用性
- **预期结果**：M1 <30s；M2 Top-3 ≥75%；M3 误报率下降 ≥40%；M4 P95 <12s + 可用性 ≥99.5%
- **断言点**：启动耗时 <30000ms；Top-3 命中率 ≥75%；误报率下降比例 ≥40%；P95 <12000ms；可用性 ≥99.5%（压测窗口失败请求占比 ≤0.5%）

### 场景 integ_ci_pipeline — CI/CD 流水线集成
- **涉及模块**：CI runner + pytest + 覆盖率门禁 + 部署
- **集成点**：CI 触发 → pytest 全量（UT + E2E + 集成）→ 覆盖率报告 → 门禁阈值 → 部署
- **测试步骤**：① 触发 CI ② 执行 pytest 全量 ③ 生成覆盖率报告 ④ 校验门禁 line+branch ≥80% ⑤ 通过后部署
- **预期结果**：pytest 全量通过，覆盖率达标，门禁放行，部署成功
- **断言点**：pytest 退出码 0；覆盖率 line+branch ≥80%；门禁通过；报告归档；部署产物存在

## 测试数据与 Mock 规范

测试数据与 Mock 基础设施统一组织于 `tests/fixtures/deploy/` 目录，按环境矩阵、降级矩阵、Bug 单、健康检查、SSE、依赖清单、启动脚本、环境变量、docker-compose 九类分文件管理；通过 `conftest.py` 注入 fixture，以 `RCA_RUNTIME_MODE` 切换三级降级模式。

### 测试数据构造策略
- **docker-compose 环境编排**：`docker-compose.yml` 拉起 Postgres 15 + Redis 7 + app 服务，集成 `conftest.py` 负责 up/等待 health/down。
- **环境变量矩阵**：以 `.env.sample` 为基线，按场景 `monkeypatch.setenv` 注入 `RCA_RUNTIME_MODE` 及各组件端点/DSN。
- **降级模式 Fixture**：三级 fixture `mode_online_full`/`mode_offline_light`/`mode_mock_demo`，分别注入对应环境变量集与 Mock 注册。
- **conftest.py**：统一提供 `tmp_path`、临时 venv、`asgi_client`、`mock_llm`、`mode_*`、`compose_up/down`、`bug_ticket` 等 fixture。

### Mock 数据样本

#### 环境矩阵配置样本（`tests/fixtures/deploy/env_matrix.json`）

```json
{
  "environment_matrix": {
    "os": {"minimum": "Win11 22H2", "recommended": "Win11 23H2+"},
    "python": {"minimum": "3.10", "recommended": "3.11"},
    "node": {"minimum": "18", "recommended": "20"},
    "postgres": {"minimum": "14", "recommended": "15"},
    "redis": {"minimum": "6", "recommended": "7"},
    "memory_gb": {"minimum": 8, "recommended": 16, "codegraph_peak": 3.2},
    "disk_gb": {"minimum": 2, "recommended": 5},
    "ports": {"app": 8000, "postgres": 5432, "redis": 6379},
    "paths": {"venv": ".venv", "repo": "AutoBugFix", "backend": "AutoBugFix/rca-backend"}
  }
}
```

#### 3 级降级矩阵配置样本（`tests/fixtures/deploy/degradation_matrix.json`）

```json
{
  "degradation_matrix": {
    "online_full": {
      "RCA_RUNTIME_MODE": "online_full",
      "RCA_CODEGRAPH_ENABLED": "true",
      "RCA_LIGHTRAG_ENABLED": "true",
      "RCA_DUAL_GATE_ENABLED": "true",
      "RCA_LLM_PROVIDER": "opencode",
      "RCA_FALLBACK_RETRIEVAL": "dual_graph_cross_validate"
    },
    "offline_light": {
      "RCA_RUNTIME_MODE": "offline_light",
      "RCA_CODEGRAPH_ENABLED": "degraded",
      "RCA_LIGHTRAG_ENABLED": "false",
      "RCA_DUAL_GATE_ENABLED": "false",
      "RCA_LLM_PROVIDER": "local_small",
      "RCA_FALLBACK_RETRIEVAL": "bm25_ripgrep"
    },
    "mock_demo": {
      "RCA_RUNTIME_MODE": "mock_demo",
      "RCA_CODEGRAPH_ENABLED": "false",
      "RCA_LIGHTRAG_ENABLED": "false",
      "RCA_DUAL_GATE_ENABLED": "false",
      "RCA_LLM_PROVIDER": "mock",
      "RCA_MOCK_DATA_PATH": "./mock_data.py",
      "RCA_FALLBACK_RETRIEVAL": "mock_data"
    }
  }
}
```

#### Bug 单样本 JSON（`tests/fixtures/deploy/bug_tickets/bug_001.json`）

```json
{
  "bug_id": "BUG-2025-0001",
  "title": "下单接口偶发 500",
  "symptom": "POST /order/create 偶发 500，日志报 NullPointerException",
  "error_type": "NullPointerException",
  "error_stack": "at com.example.order.OrderService.create(OrderService.java:42)\n  at com.example.order.OrderController.create(OrderController.java:18)",
  "suspect_service": "order-service",
  "components": ["order-service", "payment-client", "inventory-service"],
  "runtime_mode": "online_full",
  "expected_root_cause": "OrderService 未校验 payment-client 返回空指针"
}
```

#### 健康检查响应样本（`tests/fixtures/deploy/health_response.json`）

```json
{
  "status": "up",
  "components": {
    "postgres": {"status": "up", "latency_ms": 12},
    "redis": {"status": "up", "latency_ms": 3},
    "lightrag": {"status": "up", "latency_ms": 45},
    "codegraph": {"status": "up", "latency_ms": 88},
    "llm": {"status": "up", "provider": "opencode"}
  },
  "runtime_mode": "online_full",
  "version": "1.0.0"
}
```

#### SSE 事件流样本（`tests/fixtures/deploy/sse_stream_sample.txt`）

```text
event: stage
data: {"stage": 1, "name": "症状确认", "status": "done", "artifact": {"symptom": "POST /order/create 偶发 500"}}

event: stage
data: {"stage": 2, "name": "链路分析", "status": "done", "artifact": {"trace": ["order-service", "payment-client"]}}

event: stage
data: {"stage": 3, "name": "代码定位", "status": "done", "artifact": {"files": ["OrderService.java#L42"]}}

event: stage
data: {"stage": 4, "name": "根因确认", "status": "hil_wait", "top3": [{"id": "rc-1", "cause": "payment-client 返回空指针未校验", "score": 0.92}]}

event: stage
data: {"stage": 5, "name": "修复方案", "status": "done", "artifact": {"fix": "OrderService 增加空指针校验"}}

event: stage
data: {"stage": 6, "name": "HIL", "status": "done", "artifact": {"confirmed": "rc-1", "operator": "tester"}}

event: done
data: {"task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "total_elapsed_ms": 9650}
```

#### requirements.txt 样本（`tests/fixtures/deploy/requirements.txt`）

```text
fastapi==0.110.0
uvicorn[standard]==0.29.0
langgraph==0.1.4
celery==5.4.0
redis==5.0.4
psycopg2-binary==2.9.9
lightrag-hku==1.1.0
lightrag==1.1.0
pydantic==2.7.0
httpx==0.27.0
httpx-sse==0.4.0
pytest==8.2.0
pytest-asyncio==0.23.7
pytest-cov==5.0.0
playwright==1.44.0
```

#### start.ps1 / start.sh 启动脚本样本

`tests/fixtures/deploy/scripts/start.ps1.sample`：

```powershell
# 1. 前置检查
python --version | Select-String "3.1[01]" || throw "需要 Python 3.10+"

# 2. venv + 依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. 按序启动服务（postgres/redis 已由 docker-compose 拉起）
$env:RCA_RUNTIME_MODE = "online_full"
Start-Process -NoNewWindow python -ArgumentList "-m","uvicorn","app.main:app","--port","8000"

# 4. 端口检查 + 健康轮询
while (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port 8000).TcpTestSucceeded) { Start-Sleep 1 }
Invoke-RestMethod http://localhost:8000/api/v1/health

# 5. 打开交互台
Start-Process http://localhost:8000/rca-command.html
```

`tests/fixtures/deploy/scripts/start.sh.sample`：

```bash
#!/usr/bin/env bash
set -euo pipefail

python3 --version | grep -qE '3\.1[01]' || { echo "需要 Python 3.10+"; exit 1; }

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export RCA_RUNTIME_MODE=online_full
python -m uvicorn app.main:app --port 8000 &

while ! nc -z 127.0.0.1 8000; do sleep 1; done
curl -s http://localhost:8000/api/v1/health

xdg-open http://localhost:8000/rca-command.html || true
```

#### .env 配置样本（`tests/fixtures/deploy/config/.env.sample`）

```env
RCA_RUNTIME_MODE=online_full
RCA_HOST=127.0.0.1
RCA_PORT=8000
RCA_INTERACTIVE_CONSOLE_PATH=/rca-command.html
RCA_CODEGRAPH_BUILD_PEAK_MEM_GB=3.2
RCA_OFFLINE_MODEL_DIR=./offline_models
RCA_MOCK_DATA_PATH=./mock_data.py

# LLM
RCA_LLM_PROVIDER=opencode
RCA_LLM_ENDPOINT=https://api.opencode.dev/v1
RCA_LLM_AK=test_ak
RCA_LLM_SK=test_sk

# Postgres
RCA_PG_DSN=postgresql://rca:rca@127.0.0.1:5432/rca
# Redis
RCA_REDIS_URL=redis://127.0.0.1:6379/0
```

#### docker-compose.yml 样本（`tests/fixtures/deploy/docker-compose.yml`）

```yaml
version: "3.9"
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: rca
      POSTGRES_PASSWORD: rca
      POSTGRES_DB: rca
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rca"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: .
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
    environment:
      RCA_RUNTIME_MODE: ${RCA_RUNTIME_MODE:-mock_demo}
      RCA_PG_DSN: postgresql://rca:rca@postgres:5432/rca
      RCA_REDIS_URL: redis://redis:6379/0
    ports: ["8000:8000"]
```

### Mock 规范
- **online_full 模式**：不 Mock。真实全栈，使用 `testcontainers` 或 docker-compose 本地服务，真实调用 opencode LLM（测试 AK/SK）。
- **offline_light 模式**：仅 Mock LightRAG（禁用 → ripgrep/BM25 兜底），LLM 走本地小模型，其余组件真实。
- **mock_demo 模式**：Mock LLM/Trace/CodeGraph/LightRAG 全部，纯冒烟验证流程贯通，产物来自 `mock_data.py`。
- **环境变量切换**：通过 `RCA_RUNTIME_MODE=online_full|offline_light|mock_demo` 切换；fixture `mode_*` 统一注入。
- **前端 rca-command.html**：用 Playwright 或真实浏览器测试，不 Mock；断言 DOM/SSE/network。

### 测试数据库初始化
- **Postgres**：docker-compose 拉起 `postgres:15`，通过 `init.sql` 或 Alembic 迁移建表，`seed` 脚本注入标注 Bug 单与已知根因。
- **Redis**：docker-compose 拉起 `redis:7`，初始化 `RCAState` 命名空间用于断点续跑测试。
- **.env 配置**：`tests/fixtures/deploy/config/.env.sample` 提供 DSN/URL/AK/SK 基线，测试用 `monkeypatch` 覆盖敏感字段。
- **数据 seed**：`tests/fixtures/deploy/bug_tickets/*.json` 批量载入 Postgres 作为标注数据集。

### Fixture 文件组织

```text
tests/fixtures/deploy/
├── env_matrix.json              # 环境矩阵配置样本
├── degradation_matrix.json      # 3 级降级矩阵配置样本
├── bug_tickets/
│   ├── bug_001.json             # Bug 单样本（全栈 E2E）
│   └── bug_002.json
├── config/
│   └── .env.sample              # .env 配置样本
├── scripts/
│   ├── start.ps1.sample         # Windows 启动脚本样本
│   └── start.sh.sample          # WSL/Bash 启动脚本样本
├── requirements.txt             # 依赖清单 + 版本锁定样本
├── docker-compose.yml           # Postgres + Redis 服务编排
├── health_response.json         # 健康检查响应样本
└── sse_stream_sample.txt        # SSE 事件流样本
```

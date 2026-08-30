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

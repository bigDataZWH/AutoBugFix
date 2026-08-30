# Checklist

## 环境与依赖
- [ ] 环境要求矩阵已定义且量化：OS（Win11 22H2 最低 / 23H2+ 推荐，WSL2 可选）、Python（3.10 最低 / 3.11 推荐，conda 隔离）、内存（8GB 最低 / 16GB 推荐，CodeGraph 构建峰值约 3.2GB）、磁盘（2GB 最低 / 5GB SSD 推荐）、网络（可选 / 首次联机推荐）
- [ ] `requirements.txt` 已编写，含 `lightrag-hku`、`lightrag`、`FastAPI`、`uvicorn[standard]`、`LangGraph`、`Celery`、`Redis`、CodeGraph 依赖，且在 Python 3.10 与 3.11 下 `pip install` 均成功（退出码 0）
- [ ] 环境变量配置项已定义：`RCA_RUNTIME_MODE`、`RCA_HOST=127.0.0.1`、`RCA_PORT=8000`、`RCA_INTERACTIVE_CONSOLE_PATH=/rca-command.html`、`RCA_CODEGRAPH_BUILD_PEAK_MEM_GB=3.2`、`RCA_OFFLINE_MODEL_DIR`、`RCA_MOCK_DATA_PATH`
- [ ] 启动前置检查逻辑已实现：Python <3.10 失败告警、可用内存 <8GB 告警、磁盘 <2GB 中止、端口 8000 占用检测并提供 `--port` 备选

## 一键启动脚本
- [ ] `start.ps1`（Windows）已实现，执行 `git clone https://github.com/bigDataZWH/AutoBugFix.git` → `cd AutoBugFix/rca-backend` → `python -m venv .venv` → `.venv\Scripts\activate` → `pip install -r requirements.txt` → 启动 → `start http://localhost:8000/rca-command.html`
- [ ] `start.sh`（WSL / Git Bash）已实现等价流程（`source .venv/bin/activate`）
- [ ] 仓库已克隆时支持增量启动（跳过 `git clone`、复用 `.venv`、仅 `pip install` 与启动）
- [ ] 依赖安装失败时输出错误码与失败包名，提示离线模式可用预置 wheel，不继续启动
- [ ] 端口 8000 被占用时正确提示占用进程并提供 `--port` 备选
- [ ] Windows 下 `.\start.ps1` 与 WSL/Git Bash 下 `bash start.sh` 执行后，`http://localhost:8000/rca-command.html` 在 5s 内可访问（HTTP 200）

## 双模式与降级矩阵
- [ ] 联机模式（`RCA_RUNTIME_MODE=online_full`）成功对接 opencode 订阅 LLM，双图谱（CodeGraph + LightRAG）完整运行，启用双图谱交叉验证
- [ ] 离线模式预置模型与样例库（`RCA_OFFLINE_MODEL_DIR`），无网络环境可完成演示
- [ ] 网络中断自动降级：LLM 调用超时/断网 → 本地小模型兜底 + 请求合并，记录降级事件，流水线不中断
- [ ] 能力降级矩阵三档已实现且与运行模式一致：联机全量（双图谱完整 + 交叉验证）/ 离线轻量（降级 + 仅 BM25 检索 + 调用图缓存）/ 纯 Mock 演示（关闭 + `mock_data.py` 预置样例）
- [ ] 运行中切换 `RCA_RUNTIME_MODE` 提示需重启服务生效，不热切换

## 交互台与 RCA 接口
- [ ] 交互台 `http://localhost:8000/rca-command.html` 可访问，渲染 6 段流水线 + 4 Tab 报告（根因 / 知识库 / 最佳实践 / 方案）
- [ ] `POST /api/v1/rca/analyze` 提交分析任务返回 `task_id`，HTTP 202，body 含 `task_id`（uuid）与 `status=queued`
- [ ] `SSE GET /api/v1/rca/{task_id}/stream` 依次推送 6 阶段（症状确认/链路分析/代码定位/根因确认/修复方案/HIL）进度与产物 JSON，末尾 `event: done` 携带 `total_elapsed_ms`
- [ ] HIL 人工确认面板在根因确认阶段弹出，`POST /api/v1/rca/{task_id}/confirm` 回调后流水线继续下一阶段
- [ ] SSE 断线重连基于 `task_id` 与 `last-event-id` 续传，不丢阶段产物

## 端到端部署验证
- [ ] Win11 最低配置（22H2 + Python 3.10 + 8GB + 离线）启动并完成纯 Mock 演示，6 段流水线渲染正常
- [ ] 推荐配置（23H2+ + Python 3.11 + 16GB + 联机 + 5GB SSD）联机全量跑通，CodeGraph 构建峰值约 3.2GB 无 OOM
- [ ] M1 原型验证（第 1-2 周）：6 阶段全跑通，端到端延迟 <30s
- [ ] M2 引擎接入（第 3-5 周）：CodeGraph + LightRAG 接入后，Top-3 命中率 ≥75%
- [ ] M3 双图谱融合（第 6-8 周）：交叉验证 + 知识飞轮上线后，误报率相对 M2 下降 ≥40%
- [ ] M4 生产可用（第 9-12 周）：P95 端到端响应 <12s，可用性 ≥99.5%
- [ ] 核心评估指标达标：Top-1 ≥80%、Top-3 ≥95%、P95 ≤12s、误报率（双闸门后）≤5%

## 风险兜底
- [ ] 大仓库 CodeGraph 构建超时（中概率）：触发增量构建 + 缓存预热，返回部分图谱并标记 incomplete，不阻断部署
- [ ] LLM 调用配额受限（中概率）：自动切换本地小模型兜底 + 合并多请求批量推理，记录降级
- [ ] 跨语言调用链断裂（高概率）：插入桥接节点 + 触发人工标注回流任务，不中断根因定位流水线

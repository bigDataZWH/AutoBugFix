# Tasks

- [ ] Task 1: 定义 Win11 环境要求与依赖清单
  - [ ] SubTask 1.1: 编写 `requirements.txt`，含 `lightrag-hku`、`lightrag`、`FastAPI`、`uvicorn[standard]`、`LangGraph`、`Celery`、`Redis` 客户端、CodeGraph 构建与跨语言解析依赖
  - [ ] SubTask 1.2: 文档化环境要求矩阵（OS Win11 22H2 最低 / 23H2+ 推荐；Python 3.10 最低 / 3.11 推荐 conda 隔离；内存 8GB 最低 / 16GB 推荐，CodeGraph 构建峰值约 3.2GB；磁盘 2GB 最低 / 5GB SSD 推荐；网络可选 / 首次联机推荐；WSL2 可选）
  - [ ] SubTask 1.3: 定义环境变量配置项（`RCA_RUNTIME_MODE`、`RCA_HOST`、`RCA_PORT`、`RCA_INTERACTIVE_CONSOLE_PATH`、`RCA_CODEGRAPH_BUILD_PEAK_MEM_GB`、`RCA_OFFLINE_MODEL_DIR`、`RCA_MOCK_DATA_PATH`）
  - [ ] SubTask 1.4: 编写启动前置检查逻辑（Python 版本 ≥3.10、可用内存 ≥8GB、磁盘 ≥2GB、端口 8000 占用检测）
  - [ ] Verification 1: 执行 `pip install -r requirements.txt` 在 Python 3.10 与 3.11 下均成功；前置检查在低版本/低内存/端口占用场景输出明确告警并按预期中止或提示

- [ ] Task 2: 实现一键启动脚本
  - [ ] SubTask 2.1: 编写 `start.ps1`（Windows）：`git clone https://github.com/bigDataZWH/AutoBugFix.git` → `cd AutoBugFix/rca-backend` → `python -m venv .venv` → `.venv\Scripts\activate` → `pip install -r requirements.txt` → 启动服务 → `start http://localhost:8000/rca-command.html`
  - [ ] SubTask 2.2: 编写 `start.sh`（WSL / Git Bash 等价）：`git clone` → `cd rca-backend` → `python3 -m venv .venv` → `source .venv/bin/activate` → `pip install` → 启动 → 打开浏览器
  - [ ] SubTask 2.3: 实现仓库已克隆的增量启动（跳过 `git clone`、复用 `.venv`、仅 `pip install` 与启动）
  - [ ] SubTask 2.4: 实现依赖安装失败兜底（输出错误码与失败包名，提示离线模式可用预置 wheel，不继续启动）
  - [ ] SubTask 2.5: 实现端口占用检测（提示占用进程，提供 `--port` 备选参数）
  - [ ] Verification 2: Windows 下执行 `.\start.ps1` 与 WSL/Git Bash 下执行 `bash start.sh` 均能在 5s 内使 `http://localhost:8000/rca-command.html` 可访问；端口占用场景正确提示

- [ ] Task 3: 实现联机与离线双模式
  - [ ] SubTask 3.1: 离线模式预置模型与样例库（`RCA_OFFLINE_MODEL_DIR`，无网络亦可用）
  - [ ] SubTask 3.2: 联机模式对接 opencode 订阅 LLM（配置 `RCA_RUNTIME_MODE=online_full`）
  - [ ] SubTask 3.3: 实现网络中断自动降级（LLM 调用超时/断网 → 本地小模型兜底 + 请求合并，记录降级事件）
  - [ ] SubTask 3.4: 实现首次联机引导（无本地缓存时引导联机拉取模型与依赖）
  - [ ] Verification 3: 联机全量模式调用 opencode 订阅 LLM 成功；断网后自动降级至本地小模型且流水线不中断；离线模式无网络可完成演示

- [ ] Task 4: 实现能力降级矩阵
  - [ ] SubTask 4.1: 联机全量（`online_full`）：CodeGraph 完整 + LightRAG 完整 + 双图谱交叉验证
  - [ ] SubTask 4.2: 离线轻量（`offline_light`）：CodeGraph 降级 + LightRAG 降级 + 仅 BM25 检索 + 调用图缓存
  - [ ] SubTask 4.3: 纯 Mock 演示（`mock_demo`）：关闭 CodeGraph/LightRAG，加载 `mock_data.py` 预置样例
  - [ ] SubTask 4.4: 实现模式切换需重启的提示逻辑（不热切换以避免图谱状态不一致）
  - [ ] Verification 4: 三档模式分别启动后，CodeGraph/LightRAG 状态与降级矩阵一致；运行中切换模式提示需重启

- [ ] Task 5: 实现交互台 rca-command.html 与 RCA 接口
  - [ ] SubTask 5.1: 渲染 6 段流水线 + 4 Tab 报告（根因 / 知识库 / 最佳实践 / 方案）
  - [ ] SubTask 5.2: 实现 `POST /api/v1/rca/analyze` 提交分析任务返回 `task_id`（响应 202，body 含 `task_id`、`status=queued`）
  - [ ] SubTask 5.3: 实现 `SSE GET /api/v1/rca/{task_id}/stream` 流式推送 6 阶段（症状确认/链路分析/代码定位/根因确认/修复方案/HIL）进度与产物 JSON
  - [ ] SubTask 5.4: 实现 HIL 人工确认面板与 `POST /api/v1/rca/{task_id}/confirm` 回调
  - [ ] SubTask 5.5: 实现 SSE 断线重连（基于 `task_id` 与 `last-event-id` 续传，不丢阶段产物）
  - [ ] Verification 5: 交互台访问渲染 4 Tab；`POST /analyze` 返回 `task_id`；SSE 依次推送 6 阶段；HIL 面板弹出并经 `/confirm` 回调后继续下一阶段；断线重连不丢产物

- [ ] Task 6: 端到端部署验证与里程碑指标对齐
  - [ ] SubTask 6.1: Win11 最低配置（22H2 + Python 3.10 + 8GB）启动并完成 Mock 演示
  - [ ] SubTask 6.2: 推荐配置（23H2+ + Python 3.11 + 16GB）联机全量跑通，CodeGraph 构建峰值约 3.2GB 无 OOM
  - [ ] SubTask 6.3: M1 验证：6 阶段全跑通，端到端 <30s
  - [ ] SubTask 6.4: M2 验证：CodeGraph + LightRAG 接入后 Top-3 命中率 ≥75%
  - [ ] SubTask 6.5: M3 验证：双图谱融合后误报率相对 M2 下降 ≥40%
  - [ ] SubTask 6.6: M4 验证：P95 响应 <12s，可用性 ≥99.5%
  - [ ] SubTask 6.7: 风险兜底验证（CodeGraph 构建超时增量构建 + 缓存预热；LLM 配额受限本地小模型兜底；跨语言调用链断裂桥接节点 + 人工标注回流）
  - [ ] Verification 6: 最低配置 Mock 演示成功；推荐配置联机全量无 OOM；M1-M4 指标按目标值达成；三类风险兜底按预期触发

# Task Dependencies
- Task 2 depends on Task 1（依赖 requirements.txt 与环境变量定义）
- Task 3 depends on Task 1（以及 `setup-lightrag-retrieval-engine`、`build-codegraph-knowledge-graph` 的本地可用性）
- Task 4 depends on Task 3（双模式就绪后才能定义降级矩阵）
- Task 5 depends on Task 2（以及 `orchestrate-five-agent-engine`、`build-dual-gate-flywheel` 的 SSE/HIL 能力）
- Task 6 depends on Task 2、Task 3、Task 4、Task 5（端到端验证需全部就绪）

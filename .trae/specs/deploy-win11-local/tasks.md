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

- [ ] Task 7: 编写 UT 测试套件（覆盖上述 14 个用例，目标覆盖率 ≥80%）
  - [ ] SubTask 7.1: 搭建 `pytest` + `pytest-asyncio` + `pytest-cov` 框架与 `conftest.py`（fixtures：`tmp_path`、`monkeypatch`、临时 venv、mock LLM 客户端、ASGI transport）
  - [ ] SubTask 7.2: 实现 `test_env_validation`（Python 3.11 / Node 20 / Postgres 15 / Redis 7 版本矩阵检测，不满足报错）
  - [ ] SubTask 7.3: 实现 `test_requirements_install`（`pip install --dry-run` + `pip check` 校验无冲突）
  - [ ] SubTask 7.4: 实现 `test_start_ps1_script`（`PSScriptAnalyzer` 语法 + 服务启动顺序 + 端口检查）
  - [ ] SubTask 7.5: 实现 `test_start_sh_script`（`shellcheck` + `bash -n` + 启动顺序 + 端口检查）
  - [ ] SubTask 7.6: 实现 3 级降级矩阵用例 `test_degradation_matrix_online`/`offline`/`mock`（环境变量 `RCA_RUNTIME_MODE` 切换）
  - [ ] SubTask 7.7: 实现 `test_mode_switch`（运行时切换 `RCA_RUNTIME_MODE` → 需重启提示）
  - [ ] SubTask 7.8: 实现 `test_frontend_page_load`（`rca-command.html` 自包含、无外部 CDN 依赖断言）
  - [ ] SubTask 7.9: 实现 `test_api_health_check`（`GET /api/v1/health` 返回各组件状态）
  - [ ] SubTask 7.10: 实现 `test_sse_endpoint`（`POST /api/v1/rca/analyze` + `GET .../stream` → `text/event-stream`，6 阶段 + `done`）
  - [ ] SubTask 7.11: 实现 `test_port_conflict`（8000/6379/5432 占用优雅报错）
  - [ ] SubTask 7.12: 实现 `test_log_rotation`（日志超限自动轮转）
  - [ ] SubTask 7.13: 实现 `test_config_override`（环境变量 > `.env` > 默认值优先级）
  - [ ] SubTask 7.14: 配置 `pytest-cov` 覆盖率阈值 line + branch ≥80%，并在 CI 中执行
  - [ ] Verification 7: 14 个用例全部通过；覆盖率报告 line + branch ≥80%；`PSScriptAnalyzer`/`shellcheck` 无 error 级诊断

- [ ] Task 8: 编写 E2E 测试套件（覆盖上述 8 个场景，使用 docker-compose + pytest）
  - [ ] SubTask 8.1: 编写 `docker-compose.yml`（Postgres 15 + Redis 7 + app 服务）与 E2E `conftest.py`（拉起/停止 compose、等待 health 就绪）
  - [ ] SubTask 8.2: 实现 `e2e_full_local_startup`（start 脚本 → 服务启动 → health → M1 <30s）
  - [ ] SubTask 8.3: 实现 `e2e_rca_analyze_request`（前端提交 → `POST /analyze` → SSE → 方案输出）
  - [ ] SubTask 8.4: 实现 `e2e_online_mode_full`（`online_full` 双图谱全链路 → Top-3）
  - [ ] SubTask 8.5: 实现 `e2e_offline_mode_degraded`（`offline_light` LightRAG 禁用 → ripgrep 兜底 → Top-3）
  - [ ] SubTask 8.6: 实现 `e2e_mock_mode_smoke`（`mock_demo` 全 Mock 冒烟 → 验证降级路径）
  - [ ] SubTask 8.7: 实现 `e2e_milestone_kpi`（M1 <30s / M2 Top-3 ≥75% / M3 误报率降 ≥40% / M4 P95 <12s + 99.5%）
  - [ ] SubTask 8.8: 实现 `e2e_restart_recovery`（中断 → 重启 → `last-event-id` 续传 → 恢复分析）
  - [ ] SubTask 8.9: 实现 `e2e_frontend_backend_integration`（headless 浏览器 → API 请求 → SSE 渲染 → 4 Tab）
  - [ ] SubTask 8.10: 配置 E2E 在 CI 独立 stage 执行，产出并归档测试报告
  - [ ] Verification 8: 8 个场景全部通过；docker-compose 环境可重复拉起；E2E 报告归档可追溯

- [ ] Task 9: 编写跨模块集成测试套件（覆盖 8 个集成场景，使用 docker-compose + pytest + Playwright）
  - [ ] SubTask 9.1: 编写集成测试 `conftest.py`（拉起/停止 docker-compose、等待 health、注入 `mode_*` fixture、ASGI client、Playwright fixture）
  - [ ] SubTask 9.2: 实现 `integ_full_stack_startup`（start 脚本 → 6 模块服务全部启动 → 健康检查 → M1 <30s）
  - [ ] SubTask 9.3: 实现 `integ_frontend_to_backend_api`（rca-command.html → `POST /api/v1/rca/analyze` → 5-Agent 全链路 → SSE 结果）
  - [ ] SubTask 9.4: 实现 `integ_online_full_mode`（`online_full` 模式全模块真实集成：CodeGraph + LightRAG + 5-Agent + 双图谱 + 闸门 全部启用）
  - [ ] SubTask 9.5: 实现 `integ_offline_light_mode`（`offline_light` 模式降级集成：LightRAG 禁用 → ripgrep 兜底 → 5-Agent 降级路径）
  - [ ] SubTask 9.6: 实现 `integ_mock_demo_mode`（`mock_demo` 模式冒烟集成：LLM/Trace/CodeGraph 全 Mock → 流程跑通验证降级路径）
  - [ ] SubTask 9.7: 实现 `integ_restart_recovery`（服务中断 → 重启 → 断点续跑：Redis RCAState 恢复 → 5-Agent 从中断 stage 继续）
  - [ ] SubTask 9.8: 实现 `integ_milestone_kpi_verification`（4 里程碑 KPI 集成验证：M1 启动 <30s / M2 Top-3 ≥75% / M3 假阳性降低 ≥40% / M4 P95 <12s + 99.5%）
  - [ ] SubTask 9.9: 实现 `integ_ci_pipeline`（CI/CD 流水线集成：pytest 全量执行 → 覆盖率报告 → 门禁阈值 → 部署）
  - [ ] SubTask 9.10: 配置集成测试在 CI 独立 stage 执行，产出并归档测试报告
  - [ ] Verification 9: 8 个跨模块集成测试场景全部通过；docker-compose 环境可重复拉起/销毁；集成报告归档可追溯

- [ ] Task 10: 搭建测试数据与 Mock 基础设施（docker-compose 编排 + 降级模式 Fixture + 环境变量矩阵 + Mock 注册）
  - [ ] SubTask 10.1: 编写 `tests/fixtures/deploy/env_matrix.json`（Python/Node/Postgres/Redis 版本 + 端口 + 路径）
  - [ ] SubTask 10.2: 编写 `tests/fixtures/deploy/degradation_matrix.json`（online_full/offline_light/mock_demo 环境变量集）
  - [ ] SubTask 10.3: 编写 `tests/fixtures/deploy/bug_tickets/*.json`（Bug 单样本，含 error_stack/components）
  - [ ] SubTask 10.4: 编写 `tests/fixtures/deploy/health_response.json`（各组件状态 JSON）
  - [ ] SubTask 10.5: 编写 `tests/fixtures/deploy/sse_stream_sample.txt`（全栈 stage 进度 + final 结果）
  - [ ] SubTask 10.6: 编写 `tests/fixtures/deploy/requirements.txt`（依赖清单 + 版本锁定）
  - [ ] SubTask 10.7: 编写 `tests/fixtures/deploy/scripts/start.ps1.sample` 与 `start.sh.sample`（服务启动顺序 + 端口检查 + 健康轮询）
  - [ ] SubTask 10.8: 编写 `tests/fixtures/deploy/config/.env.sample`（LLM 端点/AK/SK/Postgres DSN/Redis URL/降级模式开关）
  - [ ] SubTask 10.9: 编写 `tests/fixtures/deploy/docker-compose.yml`（Postgres + Redis 服务编排 + healthcheck）
  - [ ] SubTask 10.10: 实现 Mock 注册中心（online_full 不 Mock / offline_light Mock LightRAG / mock_demo 全 Mock）与 `RCA_RUNTIME_MODE` 切换
  - [ ] SubTask 10.11: 实现测试数据库初始化（Postgres seed 注入标注 Bug 单 + Redis RCAState 命名空间初始化）
  - [ ] Verification 10: 9 类测试样本就绪；Fixture 文件组织符合 `tests/fixtures/deploy/` 约定；三级降级模式 Mock 注册可切换

# Task Dependencies
- Task 2 depends on Task 1（依赖 requirements.txt 与环境变量定义）
- Task 3 depends on Task 1（以及 `setup-lightrag-retrieval-engine`、`build-codegraph-knowledge-graph` 的本地可用性）
- Task 4 depends on Task 3（双模式就绪后才能定义降级矩阵）
- Task 5 depends on Task 2（以及 `orchestrate-five-agent-engine`、`build-dual-gate-flywheel` 的 SSE/HIL 能力）
- Task 6 depends on Task 2、Task 3、Task 4、Task 5（端到端验证需全部就绪）
- Task 9 depends on Task 8（复用 docker-compose 与 E2E conftest 基础设施）
- Task 10 depends on Task 4（降级矩阵定义后才能构建降级 Fixture）、Task 8（复用 docker-compose 编排）

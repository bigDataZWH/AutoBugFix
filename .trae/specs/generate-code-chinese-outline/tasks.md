# Tasks

- [ ] Task 1: 封装 opencode 订阅 LLM 调用层（OpenAI 兼容端点 + AK/SK 鉴权，角色级模型切换：抽取用便宜模型、推理用强模型）
  - [ ] SubTask 1.1: 实现统一 LLM client，对接 opencode 订阅模型（DeepSeek-V3 / Qwen2.5-Coder），支持 OpenAI 兼容 `/v1/chat/completions`
  - [ ] SubTask 1.2: 实现角色级模型路由：`EXTRACT_LLM_MODEL`（默认 qwen2.5-coder）用于中文化、`QUERY_LLM_MODEL`（默认 deepseek-v3）用于推理，同一 client 仅切换 `model` 字段
  - [ ] SubTask 1.3: 实现 AK/SK 鉴权与请求重试（`LLM_MAX_RETRY=3`、`LLM_TIMEOUT_MS=30000`）
  - [ ] SubTask 1.4: 实现 token 用量统计（prompt/completion token 数、模型名、角色），落盘到用量表
  - [ ] SubTask 1.5: 实现配额受限兜底（429/连续 3 次超时 → 本地小模型降级，响应标记 `degraded=true`）
  - **验证步骤**：`pytest tests/code2cn/test_llm_client.py`；构造 429 mock 验证降级路径返回 `degraded=true` 且无异常抛出；token 统计落盘可查

- [ ] Task 2: 实现函数级中文大纲生成器（code2cn 核心，方案甲）
  - [ ] SubTask 2.1: 基于 AST 切分到函数，复用 CodeGraph tree-sitter 多语言符号提取，提取函数签名 + 源码（输出 `AstFunctionNode`）
  - [ ] SubTask 2.2: 实现中文大纲 Prompt 模板（保留符号名、中文分步描述每步一句、标注参数/返回值/副作用/异常路径/外部调用）
  - [ ] SubTask 2.3: 输出结构化 `CodeOutline` JSON（symbol/file/cn_summary/external_calls/failure_paths），严格 JSON Schema 校验
  - [ ] SubTask 2.4: 大函数拆分逻辑（超 `CODE2CN_MAX_FN_LINES=200` 行按 AST 逻辑块分段，聚合 `cn_summary` 且 ≤ 512 字符）
  - [ ] SubTask 2.5: 多语言覆盖（Java/Go/Python/TypeScript）函数边界正确提取
  - [ ] SubTask 2.6: 异常路径与外部调用专项检测（try/catch、自定义抛出、DB/RPC/缓存识别进对应数组）
  - **验证步骤**：准备 4 语言各 5 个样例函数，运行生成器；`jq` 校验输出符合 `CodeOutline` schema；符号保留率脚本 `scripts/check_symbol_retention.py` 应 = 100%；外部调用检出率 ≥ 85%

- [ ] Task 3: 暴露 REST 接口与 MCP 工具
  - [ ] SubTask 3.1: 实现 `POST /api/v1/code2cn/generate`（输入 symbol/file/source_code，输出 CodeOutline JSON，含 `degraded`、`model`、`tokens`）
  - [ ] SubTask 3.2: 注册 MCP 工具 `code2cn_outline(symbol)`，返回完整 CodeOutline
  - [ ] SubTask 3.3: 接口鉴权与错误码（401 鉴权失败、503 配额耗尽且无兜底）
  - **验证步骤**：`curl -X POST /api/v1/code2cn/generate` 传入 OrderService.create 样例，校验 200 响应字段齐全；MCP 客户端调用 `code2cn_outline("OrderService.create")` 返回非空 `cn_summary`；缓存命中路径 < 50ms（基准压测脚本）

- [ ] Task 4: 实现 CodeGraph 节点语义增强适配层
  - [ ] SubTask 4.1: CodeGraph 函数节点 schema 升级：新增 `cn_summary` / `external_calls` / `failure_paths` 字段并持久化到 SQLite
  - [ ] SubTask 4.2: 实现旧节点回填迁移（首次查询识别缺失字段按需中文化，不阻塞已有查询）
  - [ ] SubTask 4.3: MCP 查询返回 `cn_summary`（命中缓存 < 50ms，未命中触发生成 < 5s）
  - **验证步骤**：对含历史无 `cn_summary` 节点的 CodeGraph 跑迁移脚本；查询任意函数节点确认 `cn_summary` 非空；缓存命中压测 P95 < 50ms

- [ ] Task 5: 实现 LightRAG 实体描述注入适配层
  - [ ] SubTask 5.1: 将"函数符号 + 中文描述"封装为 LightRAG 实体描述（实体名 `func:<symbol>`，description 为中文大纲全文）
  - [ ] SubTask 5.2: 经 `insert_custom_kg` 注入检索图谱，embedding 用 bge-m3（dim=1024）
  - [ ] SubTask 5.3: 验证中文语义检索可命中（如"创建订单时库存校验失败"召回 `func:OrderService.create`）
  - **验证步骤**：注入 20 个样例函数实体后，用 10 条中文自然语言 query 跑检索，统计 Recall@5 ≥ 阈值；确认实体 embedding 维度 = 1024

- [ ] Task 6: 实现增量中文化策略
  - [ ] SubTask 6.1: 仅对嫌疑子图函数按需触发中文化（接 A2/根因分析子图输出）
  - [ ] SubTask 6.2: 实现 `cn_summary` 缓存层（`CODE2CN_CACHE_ENABLED`），已中文化函数直接命中
  - [ ] SubTask 6.3: `git commit` 增量重建：基于 `git diff` 计算受影响子树，仅重建受影响函数及调用链上下游
  - **验证步骤**：构造 100 函数仓库，全仓中文化记 token 总量 T_full；仅对 10 函数嫌疑子图中文化记 T_inc，验证 `T_inc / T_full ≤ 30%`；模拟一次 commit 触及 3 函数，确认仅重建相关子树

- [ ] Task 7: 实现分层摘要升级路径（丙方案，大规模仓库可选）
  - [ ] SubTask 7.1: 方法摘要→类摘要→模块摘要的多 Agent 自底向上流程（Agent4cs 范式）
  - [ ] SubSubtask 7.1.1: 类摘要由其子方法大纲聚合，模块摘要由其子类大纲聚合
  - [ ] SubTask 7.2: 规模阈值触发切换开关（`CODE2CN_HIERARCHICAL_THRESHOLD=5000` 函数数）
  - [ ] SubTask 7.3: LightRAG 实体同时注入方法级与类/模块级实体
  - **验证步骤**：构造 > 5000 函数仓库，确认阈值触发切换提示；分层模式产出方法/类/模块三级实体并均可检索命中

- [ ] Task 8: 验证中文化输出质量与成本
  - [ ] SubTask 8.1: 抽样人工评审中文大纲语义准确性（目标准确率 ≥ 90%）
  - [ ] SubTask 8.2: 符号保留率校验（目标 100%，symbol/类名/函数名不被翻译）
  - [ ] SubSubtask 8.2.1: 外部调用检出率（目标 ≥ 85%）、异常路径检出率（目标 ≥ 80%）
  - [ ] SubTask 8.3: 对比全仓 vs 增量中文化的 token 成本（增量 ≤ 全仓 30%）
  - [ ] SubTask 8.4: 兜底成功率验证（配额受限场景降级成功率 ≥ 99%）
  - **验证步骤**：运行 `scripts/eval_code2cn.py` 输出质量报告（准确率/符号保留率/检出率/成本比/兜底成功率全量指标）；指标全部达标后归档报告

- [ ] Task 9: 编写 UT 测试套件（覆盖上述 10 个用例，目标覆盖率 ≥ 80%）
  - [ ] SubTask 9.1: 搭建 `tests/code2cn/` 测试目录，配置 `pytest` + `pytest-asyncio` + `pytest-mock` + `pytest-cov`
  - [ ] SubTask 9.2: 编写 `test_codeoutline_schema_validation`（缺字段 / 类型错误 / 超 512 字符 cn_summary 拒绝，合法 JSON 通过）
  - [ ] SubTask 9.3: 编写 `test_ast_function_splitter`（Java / Go / Python / TypeScript 四语言函数边界提取）
  - [ ] SubTask 9.4: 编写 `test_prompt_construction`（符号保留指令 + 外部调用标注指令占位填充）
  - [ ] SubTask 9.5: 编写 `test_llm_role_switch`（`EXTRACT_LLM_MODEL` vs `QUERY_LLM_MODEL` client 切换，断言请求体 `model` 字段）
  - [ ] SubTask 9.6: 编写 `test_llm_quota_fallback`（mock 429 / 超时 3 次 → `degraded=true`，无异常抛出）
  - [ ] SubTask 9.7: 编写 `test_external_call_detection`（DB / RPC / 缓存检出率 ≥ 85%）
  - [ ] SubTask 9.8: 编写 `test_failure_path_extraction`（try/catch / 自定义异常 / 提前 return）
  - [ ] SubTask 9.9: 编写 `test_large_function_chunking`（> 200 行拆分，聚合 `cn_summary` ≤ 512 字符）
  - [ ] SubTask 9.10: 编写 `test_cache_hit_miss`（命中 / 未命中，P95 < 50ms，采样 ≥ 100 次）
  - [ ] SubTask 9.11: 编写 `test_mcp_tool_response`（`code2cn_outline(symbol)` 响应六字段齐全 + Schema 合规）
  - [ ] SubTask 9.12: 跑 `pytest --cov=code2cn --cov-branch`，补足用例使 line + branch 覆盖率 ≥ 80%
  - **验证步骤**：`pytest tests/code2cn/ -q --cov=code2cn --cov-branch --cov-fail-under=80` 全绿；10 个 UT 用例全部通过

- [ ] Task 10: 编写 E2E 测试套件（覆盖上述 5 个场景，使用 httpx AsyncClient + testcontainers）
  - [ ] SubTask 10.1: 搭建 `tests/e2e/` 目录，配置 `httpx.AsyncClient`（`ASGITransport` 直连 FastAPI app）+ `testcontainers`（Redis 缓存 / git 仓库）
  - [ ] SubTask 10.2: 编写 `e2e_full_generate_flow`（AST → LLM → `CodeOutline` 全链路，断言全字段非空 + Schema 合规）
  - [ ] SubTask 10.3: 编写 `e2e_rest_api_endpoint`（鉴权 / 参数校验 / 400 / 429 / 500 错误码全链路）
  - [ ] SubTask 10.4: 编写 `e2e_mcp_tool_integration`（A2 Agent 经 MCP 调用 `code2cn_outline` 并消费大纲）
  - [ ] SubTask 10.5: 编写 `e2e_degraded_mode`（LLM 不可用降级，`degraded=true` + `cn_summary` 仅基础描述）
  - [ ] SubTask 10.6: 编写 `e2e_incremental_update`（`git commit` 增量重建，仅受影响子树，token ≤ 全仓 30%）
  - [ ] SubTask 10.7: 用 `respx` / `httpx_mock` 拦截 opencode 端点模拟 429 / 500；`testcontainers` 提供真实 Redis / git 环境
  - **验证步骤**：`pytest tests/e2e/ -q` 全绿；5 个 E2E 场景全部通过；CI 流水线集成 E2E 阶段

- [ ] Task 11: 编写跨模块集成测试套件（覆盖上述 5 个集成场景，使用真实 CodeGraph 输出 + mock LLM）
  - [ ] SubTask 11.1: 搭建 `tests/integration/` 目录与 `conftest.py`（CodeGraph 真实输出桩、LLM mock、LightRAG ainsert mock、MCP transport mock、tmp 数据库）
  - [ ] SubTask 11.2: 编写 `integ_codegraph_to_code2cn`（`AstFunctionNode` → 生成器入参字段映射，断言 `symbol`/`file`/`source_code` 透传 + 符号原文未翻译 + Schema 合规）
  - [ ] SubTask 11.3: 编写 `integ_code2cn_to_lightrag`（`CodeOutline` → `insert_custom_kg`，断言实体名 `func:<symbol>` + `description == cn_summary` + embedding `dim=1024` + `SIMILAR_TO` 边）
  - [ ] SubTask 11.4: 编写 `integ_code2cn_to_agent2_mcp`（A2 mock 经 MCP 调用 `code2cn_outline` 并消费大纲，断言 tool call 参数 + 响应六字段 + A2 上下文 `cn_summary` 非空 + 缓存命中 < 50ms）
  - [ ] SubTask 11.5: 编写 `integ_llm_role_integration`（`EXTRACT_LLM_MODEL` 抽取 → `QUERY_LLM_MODEL` 推理切换，断言请求体 `model` 字段 + 同一 client + token 角色区分）
  - [ ] SubTask 11.6: 编写 `integ_full_pipeline_code2cn`（CodeGraph → 中文化 → LightRAG 注入 → A2 检索消费全链路，断言端到端 `symbol` 一致 + Schema 合规 + token 累计统计正确）
  - [ ] SubTask 11.7: LLM 用 `respx` / `httpx_mock` 拦截不产生真实调用；`insert_custom_kg` mock 验证入参不实际写入图谱
  - **验证步骤**：`pytest tests/integration/ -q` 全绿；5 个集成场景全部通过；`mock.assert_called_once` 断言 LLM 未真实调用

- [ ] Task 12: 搭建测试数据与 Mock 基础设施（Fixture 工厂 + conftest.py + 样本 JSON 文件 + Mock 注册）
  - [ ] SubTask 12.1: 实现 Fixture 工厂函数（`make_ast_node` / `make_code_outline` / `make_llm_response` / `make_mcp_message`），支持参数化边界覆盖（缺字段、超长 `cn_summary`、空数组）
  - [ ] SubTask 12.2: 注册 `tests/conftest.py` 与 `tests/integration/conftest.py` 共享 fixture（LLM mock client、CodeGraph 符号桩、LightRAG ainsert mock、MCP transport mock、tmp 数据库），目标被测组件 fixture 覆盖率 ≥ 90%
  - [ ] SubTask 12.3: 生成样本 JSON 文件置于 `tests/fixtures/code2cn/`（`code_outline` / `ast_node` / `llm_response` / `mcp` 四类，含 Java/Go/Python 多语言节点）
  - [ ] SubTask 12.4: 实现 opencode LLM mock（OpenAI 兼容端点拦截，按角色返回预设 `CodeOutline` JSON，含 429 / 超时场景）
  - [ ] SubTask 12.5: 实现 CodeGraph symbol mock（预设 `symbol`/`file`/`source_code`）与 LightRAG `insert_custom_kg` mock（验证调用参数不实际写入）
  - [ ] SubTask 12.6: 实现 MCP 传输层 mock（验证 `tools/call` 参数与响应序列化）
  - [ ] SubTask 12.7: 测试数据库初始化（SQLite `:memory:` + `tmp_path` 临时目录，mock embedding `dim=1024`）
  - [ ] SubTask 12.8: 样本 JSON 经 Schema 校验入库（`CodeOutline` / `AstFunctionNode` 各自 schema 通过）
  - **验证步骤**：共享 fixture 覆盖率 ≥ 90%（被测组件均有对应 fixture）；CodeOutline/AstFunctionNode/LLM 响应/MCP 四类 Mock 样本就绪；`tests/fixtures/code2cn/*.json` 路径约定通过校验

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 2
- Task 5 depends on Task 2、Task 4（以及 setup-lightrag-retrieval-engine 完成）
- Task 6 depends on Task 2、Task 3、Task 4
- Task 7 depends on Task 2
- Task 8 depends on Task 2、Task 3、Task 4、Task 5、Task 6
- Task 9 depends on Task 1、Task 2、Task 3
- Task 10 depends on Task 9、Task 1、Task 2、Task 3、Task 4、Task 6
- Task 11 depends on Task 9、Task 2、Task 3、Task 4、Task 5
- Task 12 depends on Task 9

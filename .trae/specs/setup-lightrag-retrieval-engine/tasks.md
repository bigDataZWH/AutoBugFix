# Tasks

- [ ] Task 1: LightRAG 环境部署与存储就绪
  - [ ] SubTask 1.1: 在 Win11 执行 `pip install lightrag-hku`，校验版本与依赖完整性
  - [ ] SubTask 1.2: 准备 Postgres 实例并启用 pgvector 扩展（KV + 向量 + 图状态单库）
  - [ ] SubTask 1.3: 配置 `working_dir` 与存储后端连接串，单进程初始化 `LightRAG(...)`
  - [ ] SubTask 1.4: 清理历史 RAGFlow Docker 残留（端口/卷），避免冲突
  - [ ] 验证: 单进程启动成功，Postgres 一体库 KV/向量/图三域可读写，`docker ps` 无相关容器，无需 WSL2/vm.max_map_count

- [ ] Task 2: 对接 opencode 订阅 LLM 与嵌入
  - [ ] SubTask 2.1: 配置 `.env`（LLM_BINDING=openai、LLM_BINDING_HOST、LLM_BINDING_API_KEY、LLM_MODEL=deepseek-v3）
  - [ ] SubTask 2.2: 配置嵌入（EMBEDDING_BINDING=openai、EMBEDDING_MODEL=bge-m3、EMBEDDING_DIM=1024、EMBEDDING_BINDING_HOST）
  - [ ] SubTask 2.3: 实现 `opencode_llm` / `maas_bge_m3` async 适配（OpenAI 兼容端点 + AK/SK 鉴权）
  - [ ] SubTask 2.4: 实现角色级模型切换（EXTRACT_LLM_MODEL=qwen2.5-coder，QUERY_LLM_MODEL=deepseek-v3）注入对应 `llm_model_func`
  - [ ] SubTask 2.5: 实现配额受限兜底（429 指数退避重试 / 降级备用模型 + 降级标记）
  - [ ] 验证: 抽取日志显示 qwen2.5-coder、推理显示 deepseek-v3、嵌入维度恒为 1024；AK/SK 失效即中止；模拟 429 可降级并标注

- [ ] Task 3: 实现三路检索分层路由
  - [ ] SubTask 3.1: 历史经验匹配（low-level + hybrid，`QueryParam(mode="hybrid")`，数据源历史工单文档向量）
  - [ ] SubTask 3.2: 根因传播追溯（图谱遍历含 CodeGraph 调用边，`QueryParam(mode="hybrid")`）
  - [ ] SubTask 3.3: 全局架构理解（high-level 主题/社区摘要 + 代码中文大纲聚合，`QueryParam(mode="high_level")`）
  - [ ] SubTask 3.4: 路由层 mode 枚举校验（hybrid | low_level | high_level）与空结果回退标注
  - [ ] 验证: 三路意图各自命中预期数据源，非法 mode 被拒，空结果有回退标注

- [ ] Task 4: 实现 CodeGraph 调用图注入
  - [ ] SubTask 4.1: 封装 `ast_kg` 结构（entities: entity_name/type/description 中文大纲；relationships: src_id/tgt_id/description=calls/weight）
  - [ ] SubTask 4.2: 实现注入适配层校验（type/description 非空，weight 正浮点，缺失按默认填充）
  - [ ] SubTask 4.3: 调用 `await rag.ainsert_custom_kg(ast_kg)` 注入结构域
  - [ ] SubTask 4.4: 实现重复注入幂等（去重/更新，不产生重复边）
  - [ ] 验证: 图谱存在 `func:OrderService.create`→`func:PaymentClient.charge`（calls, weight=8.0），description 为中文大纲；重复注入不重复；空调用图不阻断

- [ ] Task 5: 实现历史问题单文本索引
  - [ ] SubTask 5.1: 批量文档封装（根因/验证/代码片段字段结构化）
  - [ ] SubTask 5.2: 调用 `await rag.ainsert(docs)` 批量索引到文本域
  - [ ] SubTask 5.3: 实现超大批量分页/分批与单批 token/内存控制
  - [ ] SubTask 5.4: 实现增量追加去重与空文档/非法条目跳过记录
  - [ ] 验证: low-level + hybrid 可命中已索引工单，返回带原工单标识；分批与去重生效

- [ ] Task 6: 启用重排与增量更新
  - [ ] SubTask 6.1: 开启默认 rerank，输出相关/模糊/不相关三档
  - [ ] SubTask 6.2: 实现重排阈值边界归档（≥阈值=相关）
  - [ ] SubTask 6.3: 实现重排器超时/异常降级（返回未重排原始排序 + 标注）
  - [ ] SubTask 6.4: 实现飞轮回写增量更新（仅刷新受影响社区摘要）
  - [ ] 验证: 三档打分可用、边界归档正确；飞轮回写仅受影响社区重算，全量重建触发率为 0

- [ ] Task 7: 验证检索质量与成本
  - [ ] SubTask 7.1: 验证三路检索意图命中率（历史经验匹配/根因传播追溯/全局架构理解）
  - [ ] SubTask 7.2: 对比 GraphRAG 索引成本（目标 ≤ 1/30）
  - [ ] SubTask 7.3: 验证调用图注入为真实调用关系（非幻觉）抽样核对
  - [ ] SubTask 7.4: 验证 Win 原生部署零 Docker 依赖与单进程资源占用
  - [ ] 验证: 索引成本 ≤ 1/30 GraphRAG；三路命中率达标；抽样注入实体可追溯到 CodeGraph 真实调用

- [ ] Task 8: 编写 UT 测试套件（覆盖 12 个用例，目标覆盖率 ≥85%）
  - [ ] SubTask 8.1: 搭建测试基础设施（pytest + pytest-asyncio 配置、conftest.py fixtures、testcontainers Postgres 启停）
  - [ ] SubTask 8.2: 实现 mock 层（mock `opencode_llm` 固定抽取/查询响应、mock `maas_bge_m3` 返回 1024 维向量、mock 重排器打分）
  - [ ] SubTask 8.3: 编写 `test_ast_kg_schema_validation`（`parametrize` 合法/非法矩阵：entities/edges 必填、实体类型枚举、边方向校验、weight 正浮点）
  - [ ] SubTask 8.4: 编写 `test_lightrag_init`（Postgres 连接池、embedding 模型加载、`working_dir` 创建、`embedding_dim==1024` 断言）
  - [ ] SubTask 8.5: 编写 `test_ainsert_async`（文本切片、实体抽取、关系抽取、社区聚类，断言 KV/向量/图三域写入）
  - [ ] SubTask 8.6: 编写 `test_ainsert_custom_kg`（ast_kg JSON → 实体/边写入 Postgres，幂等不重复、空调用图不阻断）
  - [ ] SubTask 8.7: 编写 `test_aquery_naive` / `test_aquery_local` / `test_aquery_hybrid`（三路检索模式分别覆盖，断言命中实体/子图/双层融合排序）
  - [ ] SubTask 8.8: 编写 `test_query_param_config`（mode 枚举校验、top_k/similarity_threshold/only_need_context 合法/边界/非法）
  - [ ] SubTask 8.9: 编写 `test_embedding_dim`（BGE-M3 dim=1024 断言、空文本不报错）
  - [ ] SubTask 8.10: 编写 `test_postgres_storage`（实体表/边表/社区表 CRUD、upsert 幂等、受影响行数）
  - [ ] SubTask 8.11: 编写 `test_retrieval_routing`（3 路检索路由决策分流：naive/local/hybrid 按查询类型、空结果回退标注）
  - [ ] SubTask 8.12: 编写 `test_reranker_triage`（重排序三分类、边界归档≥阈值=相关、超时降级返回未重排排序+标注）
  - [ ] SubTask 8.13: 接入 `pytest --cov` 覆盖率统计，达标 line + branch ≥ 85%
  - [ ] 验证: 12 个 UT 用例全部通过；覆盖率 line + branch ≥ 85%；embedding 维度 dim=1024 断言通过；3 路路由 UT 全覆盖；重排序三分类归档正确率 100%

- [ ] Task 9: 编写 E2E 测试套件（覆盖 6 个场景，使用 testcontainers Postgres）
  - [ ] SubTask 9.1: 搭建 E2E 环境（testcontainers Postgres + pgvector 自动起停、本地 BGE-M3 或 mock 1024 维、LLM mock 与 opencode 端点 CI 标记区分）
  - [ ] SubTask 9.2: 编写 `e2e_full_retrieval_flow`（code2cn 输出 → `ainsert_custom_kg` 注入 → `aquery` 查询 → 排序结果校验）
  - [ ] SubTask 9.3: 编写 `e2e_hybrid_query`（hybrid 双层融合：low-level 实体 + high-level 社区摘要合并去重与重排）
  - [ ] SubTask 9.4: 编写 `e2e_custom_kg_inject`（CodeGraph CPG → ast_kg 转换 → `ainsert_custom_kg` → 可检索，断言真实调用关系非幻觉）
  - [ ] SubTask 9.5: 编写 `e2e_chinese_query_retrieval`（中文 query 命中中文大纲实体，根因传播路径含注入调用边，中文编码无乱码）
  - [ ] SubTask 9.6: 编写 `e2e_incremental_insert`（新增函数大纲后受影响社区摘要刷新、未受影响社区 hash 一致、全量重建触发率=0）
  - [ ] SubTask 9.7: 编写 `e2e_reranker_gate`（relevant 直通 / ambiguous 触发补充检索 / irrelevant 拒绝 + 重排器超时降级标注）
  - [ ] 验证: 6 个 E2E 场景全部通过；testcontainers Postgres 自动起停；各场景断言点全部满足

- [ ] Task 10: 编写跨模块集成测试套件（覆盖 6 个集成场景，使用 testcontainers Postgres）
  - [ ] SubTask 10.1: 搭建集成测试环境（testcontainers Postgres + pgvector、session 级容器 fixture、schema/数据隔离、真实 SQL 不 mock DB）
  - [ ] SubTask 10.2: 编写 `integ_code2cn_to_lightrag_ainsert`（CodeOutline JSON → `ainsert_custom_kg` 实体描述字段映射，断言 description == 中文大纲原文、无截断/乱码、无幻觉实体）
  - [ ] SubTask 10.3: 编写 `integ_codegraph_astkg_to_lightrag`（ast_kg JSON → `ainsert_custom_kg`，CPG 实体/边落库、calls 边 weight=8.0、重复注入幂等不重复、可追溯到 CodeGraph CPG 原始节点）
  - [ ] SubTask 10.4: 编写 `integ_flywheel_to_lightrag_ainsert`（飞轮回写实体 → `ainsert` 增量 + SIMILAR_TO 边创建、相似度 ≥ threshold、受影响社区摘要刷新、未受影响社区 hash 不变）
  - [ ] SubTask 10.5: 编写 `integ_lightrag_to_agent4_aquery`（A4 经 `aquery` hybrid 检索根因候选，QueryParam 契约校验、top_k 截断、结果含根因传播路径）
  - [ ] SubTask 10.6: 编写 `integ_lightrag_to_crag_reranker`（检索结果 → CRAG 三分类 relevant/ambiguous/irrelevant、边界分数=阈值归 relevant、ambiguous 触发补充检索、超时降级标注）
  - [ ] SubTask 10.7: 编写 `integ_full_pipeline_lightrag`（code2cn + ast_kg 注入 → `aquery` → CRAG 全链路，断言各环节衔接、无数据丢失/字段错位、relevant 集含目标根因）
  - [ ] 验证: 6 个跨模块集成场景全部通过；testcontainers Postgres 走真实 SQL；各集成点字段映射与契约断言满足

- [ ] Task 11: 搭建测试数据与 Mock 基础设施（Postgres testcontainers + Fixture 工厂 + Mock 注册）
  - [ ] SubTask 11.1: 搭建 Postgres testcontainers 启停 fixture（pgvector 扩展、session 级、会话结束 `docker rm -f` 自动销毁、teardown 无残留验证）
  - [ ] SubTask 11.2: 执行 LightRAG schema DDL + 测试数据 seed（实体/边/社区表、`vector(1024)` 列、HNSW/IVFFlat 向量索引、基线行数与社区摘要 hash 记录）
  - [ ] SubTask 11.3: 实现实体/边/社区 Fixture 工厂（工厂函数、字段 override、批量生成，替代散落硬编码）
  - [ ] SubTask 11.4: 注册 BGE-M3 embedding mock（按输入文本 hash 生成确定性 dim=1024 向量、不加载真实模型、`len(vec)==1024` 断言）
  - [ ] SubTask 11.5: 注册 LightRAG 内部 LLM 抽取 mock（实体/关系抽取返回预设 JSON、角色级区分 EXTRACT_LLM_MODEL=qwen2.5-coder / QUERY_LLM_MODEL=deepseek-v3、不调用真实 opencode 端点）
  - [ ] SubTask 11.6: 注册 CRAG reranker mock（三分类预设标签、阈值边界归档、超时/异常降级分支）
  - [ ] SubTask 11.7: 组织 Fixture 文件（`tests/fixtures/lightrag/*.json` + `ast_kg/` 子目录样本，`conftest.py` 按需加载静态 JSON）
  - [ ] 验证: testcontainers 启停无残留；embedding dim=1024 校验通过；6 类 Mock 样本 JSON 就绪；Fixture 工厂可批量生成与字段覆盖

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 2（以及 build-codegraph-knowledge-graph 调用图输出、generate-code-chinese-outline 中文大纲）
- Task 5 depends on Task 2
- Task 6 depends on Task 3、Task 4
- Task 7 depends on Task 3、Task 4、Task 5、Task 6
- Task 8 depends on Task 1、Task 2、Task 3、Task 4、Task 6
- Task 9 depends on Task 8
- Task 11 depends on Task 8
- Task 10 depends on Task 9、Task 11

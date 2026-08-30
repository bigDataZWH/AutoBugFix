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

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 2（以及 build-codegraph-knowledge-graph 调用图输出、generate-code-chinese-outline 中文大纲）
- Task 5 depends on Task 2
- Task 6 depends on Task 3、Task 4
- Task 7 depends on Task 3、Task 4、Task 5、Task 6

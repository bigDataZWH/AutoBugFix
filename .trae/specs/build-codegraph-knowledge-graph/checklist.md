# Checklist · CodeGraph 代码图谱构建

## 安装与初始化
- [ ] Windows 经 `irm .../install.ps1 | iex` 安装 codegraph CLI 成功，`codegraph --version` 输出非空
- [ ] Linux/macOS 经等价 install.sh 安装成功，`codegraph --version` 输出非空
- [ ] codegraph CLI 版本与 opencode-codegraph@0.1.38 主版本兼容（无版本冲突警告）
- [ ] tree-sitter 多语言解析覆盖 ≥20 种语言（Java/Python/Go/TypeScript/Rust/C++ 等代表性语言各产出符号）

## 插件与 MCP Server 注册
- [ ] opencode.json 注册 `plugin: ["opencode-codegraph"]`，JSON 语法校验通过
- [ ] opencode.json 配置 `mcp.codegraph`：command="codegraph"、args=["mcp"]、env.CODEGRAPH_DB="./.codegraph/graph.db"
- [ ] npm 包版本锁定为 opencode-codegraph@0.1.38（package.json/锁文件可查）
- [ ] opencode 启动后日志确认 codegraph MCP Server 就绪
- [ ] 对话中提及源文件即自动注入 CPG 上下文，上下文含 方法数/圈复杂度/fan_in·fan_out/安全发现/调用关系 5 类字段
- [ ] DB 路径未初始化时输出「请先执行 codegraph init -i 与 codegraph index」提示，不崩溃
- [ ] 插件版本低于 0.1.38 时输出版本冲突警告并阻止降级运行

## 六步自动构建流程
- [ ] STEP1 opencode 拉取 repo@branch 到工作区成功（git clone -b 生效）
- [ ] STEP2 `codegraph init -i` 初始化 .codegraph 目录成功
- [ ] STEP3 `codegraph index` 全量构建：tree-sitter 解析全仓→符号/边→SQLite 写入成功
- [ ] STEP4 函数节点语义中文化：每个函数符号节点含 cn_summary 字段（对接 generate-code-chinese-outline）
- [ ] STEP5 「函数符号 + 中文描述」经 insert_custom_kg 注入 LightRAG 检索图谱成功（对接 setup-lightrag-retrieval-engine）
- [ ] STEP6 git commit 后增量重建：仅重建受影响子树，不进行全量重建
- [ ] 六步流水线完整跑通，无中断

## 增量与容错
- [ ] 增量重建耗时显著低于全量构建（变更文件集子树重建，阈值对比记录在案）
- [ ] 大仓库（~10k 文件）全量构建超时阈值时自动降级为增量 + 缓存预热，输出部分可用图谱并标记未完成范围
- [ ] 构建中断（OOM/kill/断电）后重启断点续建，基于 SQLite 已持久化部分图谱，不从零全量重建
- [ ] tree-sitter 解析失败/SQLite 写入异常时记录失败文件与原因到日志，跳过继续，最终汇总失败清单

## MCP 工具查询能力
- [ ] `codegraph_callers(symbol, depth)` 返回多层调用者列表，含 fan_in/fan_out/圈复杂度/方法数/安全发现
- [ ] `codegraph_callees(symbol)` 返回被调用者列表及调用边
- [ ] `codegraph_explore(symbol)` 返回以该符号为中心的 N 跳邻居子图（nodes + edges + center）
- [ ] `codegraph_taint(entry, sink)` 返回污点传播路径，每跳含 函数/行号/type，含 reachable 可达性标记
- [ ] 反向 BFS 构建静态嫌疑函数集 S_static 可用（基于报错栈符号反向追溯调用者）
- [ ] 跨语言调用链断裂（RPC/FFI 边界）插入 bridge node 并触发人工标注回流
- [ ] 查询不存在符号时返回结构化错误「symbol not found」及相近符号建议列表，而非空结果或异常

## SQLite 图谱存储与离线运行
- [ ] .codegraph/graph.db 含 nodes 表（symbol/type/file/line/fan_in/fan_out/complexity/cn_summary）
- [ ] .codegraph/graph.db 含 edges 表（src_id/tgt_id/type/weight）及 idx_edges_src/idx_edges_tgt 索引
- [ ] .codegraph/graph.db 含 nodes_fts 虚拟表（FTS5，索引 symbol/cn_summary/file）
- [ ] FTS5 全文搜索按符号关键词与中文 cn_summary 检索命中相关节点（命中数 ≥1）
- [ ] 关系图多跳查询（call/inherit/ref 边遍历）正确返回路径
- [ ] 无网络环境下 callers/explore/taint 均可查询（100% 本地运行，零远程依赖）
- [ ] DB 损坏/锁死时可基于源码重新索引重建，旧 DB 备份供诊断，重建后数据一致

## 性能与基准验收
- [ ] 大型仓库（~10k 文件）下 agent 回答架构问题几乎零文件读取（文件读取次数趋近 0）
- [ ] 大仓库全量构建峰值内存约 3.2GB（≤3.5GB 上限，不 OOM）
- [ ] 语言覆盖 ≥20 种（tree-sitter 语法覆盖清单可查）
- [ ] 相比 V2.0 手写 tree-sitter，7 个真实开源仓库中位数：成本节省 ≥35%
- [ ] 相比 V2.0，token 节省 ≥59%
- [ ] 相比 V2.0，耗时节省 ≥49%
- [ ] 相比 V2.0，工具调用减少 ≥70%
- [ ] ~10k 文件图谱单次多跳查询秒级响应（无需读取原始源文件）

## 测试方案验收
- [ ] UT 覆盖率 ≥85%（line + branch，coverage 报告可查）
- [ ] 12 个 UT 用例全部通过（pytest 执行退出码 0）
- [ ] 6 个 E2E 场景全部通过
- [ ] 多语言解析 UT 覆盖 Java/Go/Python/TS/JS/C++ 六语言（各产出符号断言）
- [ ] MCP 4 工具 callers/callees/explore/taint UT 全覆盖（4/4）
- [ ] 内存峰值 ≤3.2GB 验证通过（tracemalloc/resource 采样峰值断言）
- [ ] 增量构建 E2E 验证仅重建变更子树（未变更节点哈希前后一致）
- [ ] CI 流水线集成 pytest 执行（CI job 跑 UT+E2E 并上报覆盖率）

## 跨模块集成测试与 Mock 验收
- [ ] 6 个跨模块集成测试场景全部通过（integ_git_to_codegraph / integ_codegraph_to_code2cn / integ_codegraph_to_lightrag_astkg / integ_codegraph_to_agent2_mcp / integ_codegraph_to_dualgraph_static / integ_full_pipeline_codegraph）
- [ ] CPG节点/边/ast_kg/MCP响应 4 类 Mock 样本 JSON 就绪（cpg_nodes.json / cpg_edges.json / ast_kg.json / mcp_responses.json）
- [ ] 6 语言代码样本就绪（Java/Go/Python/TS/JS/C++，tests/fixtures/codegraph/samples/<lang>/ 各含一份）
- [ ] SQLite :memory: + FTS5 测试库初始化通过（nodes/edges/nodes_fts 三表 DDL 可建可查）
- [ ] 上下游数据契约字段映射集成测试通过（symbol/file/source_code 传入 code2cn；ast_kg 注入 LightRAG；S_static func_id/func_name/call_path/static_depth 字段齐全）
- [ ] Fixture 文件组织符合 tests/fixtures/codegraph/ 约定（samples/ 多语言样本 + 4 类 JSON 数据样本 + opencode.json）
- [ ] MCP mock 不产生真实进程间通信（传输层 mock 验证 tool call 参数与响应格式，无真实 IPC 调用）

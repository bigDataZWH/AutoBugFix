# Tasks

- [ ] Task 1: 安装与初始化 CodeGraph（Win11 install.ps1 / Linux 等价脚本）
  - [ ] SubTask 1.1: Windows 执行 `irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex` 安装 codegraph CLI
  - [ ] SubTask 1.2: Linux/macOS 执行等价 `install.sh` 安装 codegraph CLI
  - [ ] SubTask 1.3: 验证 `codegraph --version` 可用且版本与 opencode-codegraph@0.1.38 主版本兼容
  - [ ] SubTask 1.4: 准备多语言测试仓（Java/Python/Go/TypeScript 各一份），验证 tree-sitter 分别解析各语言 AST
  - **验证**: `codegraph --version` 输出非空且无报错；多语言测试仓每个语言文件均能产出符号，覆盖 20+ 语言中的代表性子集

- [ ] Task 2: 注册 opencode-codegraph 插件与 MCP Server
  - [ ] SubTask 2.1: 编写 opencode.json，注册 `plugin: ["opencode-codegraph"]`
  - [ ] SubTask 2.2: 配置 `mcp.codegraph`（command: "codegraph"、args: ["mcp"]、env.CODEGRAPH_DB: "./.codegraph/graph.db"）
  - [ ] SubTask 2.3: 锁定 npm 包版本 opencode-codegraph@0.1.38
  - [ ] SubTask 2.4: 验证 opencode 启动后 MCP Server 就绪
  - [ ] SubTask 2.5: 验证对话中提及文件即自动注入 CPG 上下文（方法数/圈复杂度/fan_in·fan_out/安全发现/调用关系）
  - [ ] SubTask 2.6: 验证 DB 路径未初始化时给出「请先执行 codegraph init -i 与 codegraph index」提示，而非崩溃
  - [ ] SubTask 2.7: 验证插件版本低于 0.1.38 时输出版本冲突警告并阻止降级运行
  - **验证**: opencode.json 语法校验通过；启动日志确认 codegraph MCP Server 就绪；提及文件后上下文中出现 fan_in/fan_out/complexity 字段

- [ ] Task 3: 实现克隆代码仓后六步自动构建流程
  - [ ] SubTask 3.1: STEP1 opencode 拉取 repo@branch 到工作区（`git clone -b $branch $repo`）
  - [ ] SubTask 3.2: STEP2 在仓库根目录执行 `codegraph init -i` 初始化 .codegraph 目录
  - [ ] SubTask 3.3: STEP3 执行 `codegraph index` 全量构建（tree-sitter 解析全仓→符号/边→SQLite 写入 .codegraph/graph.db）
  - [ ] SubTask 3.4: STEP4 触发函数节点语义中文化，为每个函数符号节点生成 cn_summary（对接 generate-code-chinese-outline，按需中文化嫌疑子图控制 token）
  - [ ] SubTask 3.5: STEP5 将「函数符号 + 中文描述」经 insert_custom_kg 注入 LightRAG 检索图谱（对接 setup-lightrag-retrieval-engine）
  - [ ] SubTask 3.6: STEP6 实现 git commit 后增量重建受影响子树（按变更文件集计算依赖符号子树，仅重建该子树）
  - [ ] SubTask 3.7: 实现大仓库构建超时兜底（全量超阈值时降级为增量 + 缓存预热，复用已索引子树，输出部分图谱并标记未完成范围）
  - [ ] SubTask 3.8: 实现构建中断恢复（基于 SQLite 已持久化部分图谱断点续建，不从零全量重建）
  - [ ] SubTask 3.9: 实现构建失败错误上报（记录失败文件与原因到日志，跳过继续，汇总失败清单）
  - **验证**: 六步流水线完整跑通；graph.db 生成且含 nodes/edges/nodes_fts 三表；增量重建耗时显著低于全量；超时兜底与中断恢复机制各触发一次并验证产物有效

- [ ] Task 4: 实现 MCP 工具查询适配层
  - [ ] SubTask 4.1: 封装 `codegraph_callers(symbol, depth)` 查询，返回多层调用者列表含 fan_in/fan_out/圈复杂度/方法数/安全发现
  - [ ] SubTask 4.2: 封装 `codegraph_callees(symbol)` 查询，返回被调用者列表及调用边
  - [ ] SubTask 4.3: 封装 `codegraph_explore(symbol)` 查询，返回以该符号为中心的 N 跳邻居子图（节点 + 边）
  - [ ] SubTask 4.4: 封装 `codegraph_taint(entry, sink)` 污点分析，返回从 entry 到 sink 的传播路径（每跳函数 + 行号 + reachable 标记）
  - [ ] SubTask 4.5: 实现反向 BFS 构建 A2 静态嫌疑函数集 S_static（基于报错栈符号反向追溯调用者）
  - [ ] SubTask 4.6: 实现跨语言调用链断裂桥接（RPC/FFI 边界插入 bridge node + 人工标注回流补全跨语言边）
  - [ ] SubTask 4.7: 实现不存在符号的结构化错误返回「symbol not found」及相近符号建议列表
  - **验证**: 四个 MCP 工具各执行一次返回字段齐全（fan_in/fan_out/complexity/methods/security_findings）；污点路径含可达性 reachable；不存在符号返回结构化错误而非异常

- [ ] Task 5: 验证 SQLite 图谱存储与离线运行
  - [ ] SubTask 5.1: 验证 .codegraph/graph.db 含 nodes 表、edges 表（含 idx_edges_src/idx_edges_tgt 索引）、nodes_fts 虚拟表（FTS5）
  - [ ] SubTask 5.2: 验证 FTS5 全文搜索（按符号关键词与中文 cn_summary 检索）命中相关节点
  - [ ] SubTask 5.3: 验证关系图多跳查询（call/inherit/ref 边遍历）正确返回路径
  - [ ] SubTask 5.4: 验证无网络环境下图谱仍可经 MCP 工具查询（断网后 callers/explore/taint 均可用）
  - [ ] SubTask 5.5: 验证 DB 损坏/锁死时可基于源码重新索引重建，并备份旧 DB 供诊断
  - **验证**: 三表结构与索引存在；FTS5 命中非空；离线查询成功；DB 重建后数据一致

- [ ] Task 6: 性能与基准验证
  - [ ] SubTask 6.1: 在大型仓库（如 VS Code ~10k 文件）验证 agent 零文件读取即可回答架构问题
  - [ ] SubTask 6.2: 监控大仓库全量构建峰值内存，确认约 3.2GB 上限不 OOM
  - [ ] SubTask 6.3: 验证 20+ 语言语法覆盖（tree-sitter 各语言 AST 解析产出符号）
  - [ ] SubTask 6.4: 对比 V2.0 手写 tree-sitter 方案，在 7 个真实开源仓库上采集成本/token/耗时/工具调用中位数
  - [ ] SubTask 6.5: 验证基准达标（平均省 35% 成本 / 59% token / 49% 耗时 / 70% 工具调用）
  - **验证**: ~10k 文件仓库零文件读取回答架构问题；峰值内存 ≤3.5GB；语言覆盖 ≥20；四项基准中位数达目标值

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 3
- Task 5 depends on Task 3
- Task 6 depends on Task 4、Task 5
- Task 3.4 依赖 generate-code-chinese-outline
- Task 3.5 依赖 setup-lightrag-retrieval-engine

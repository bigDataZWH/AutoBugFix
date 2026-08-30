# AutoBugFix — 问题单解决平台

输入问题单链接与代码仓（分支），自动分析根因、匹配知识库历史经验、探索业界最佳实践、给出设计方案。

## 功能

- **根因分析**：拉取 CodeHub MR（diff / 评论 / 变更文件全文），LLM 结构化输出根因 + 证据定位
- **知识库匹配**：ChromaDB 向量检索历史问题单（根因 / 验证 / 代码），返回相似案例
- **最佳实践探索**：可选联网搜索（DuckDuckGo / Tavily）+ LLM 综合提炼
- **设计方案**：融合根因 + 历史案例 + 最佳实践，输出代码变更方案 + 验证建议
- **知识库管理**：JSON / CSV 批量入库、语义检索、统计、增删
- **本地部署**：Docker 一键启动，或裸机 `./run.sh`

## 快速开始

### 方式一：Docker（推荐）

```bash
cp backend/.env.example backend/.env   # 按需编辑配置
docker compose up --build -d
```

打开 http://localhost:8000

### 方式二：本地脚本

```bash
./run.sh            # 生产模式: 构建前端 + 启动后端 (单端口 8000)
./run.sh dev        # 开发模式: 前端 5173 + 后端 8000 热更新
./run.sh ingest     # 导入样例知识库
./run.sh ingest backend/data/samples/knowledge_base.json   # 导入指定文件
```

## 配置说明

编辑 `backend/.env`：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_BASE_URL` | LLM 网关 (OpenAI 兼容) | `http://localhost:11434/v1` |
| `LLM_API_KEY` | LLM 密钥 | `ollama` |
| `LLM_MODEL` | 模型名 | `qwen2.5:7b` |
| `EMBED_PROVIDER` | 向量模式 `api` / `local` | `api` |
| `EMBED_MODEL` | 向量模型 | `BAAI/bge-small-zh-v1.5` |
| `CODEHUB_BASE_URL` | CodeHub 地址 | `https://codehub.example.com` |
| `CODEHUB_TOKEN` | CodeHub 私有 token | (空, 走 mock) |
| `CODEHUB_MOCK` | 无 token 时用内置样例 | `true` |
| `WEB_SEARCH_PROVIDER` | 联网搜索 `none` / `ddgs` / `tavily` | `none` |

> 华为内部大模型 / 外部 API / 本地 Ollama 均走 OpenAI 兼容协议，改 `LLM_BASE_URL` 即可切换。

## 知识库数据格式

JSON（数组或 `{"records":[...]}`）：

```json
{
  "records": [
    {
      "title": "fix: 空指针异常导致 500",
      "summary": "接口在用户不存在时抛出 AttributeError",
      "root_cause": "未对 db.query 返回值做空值守卫 ...",
      "verification": "1. 构造不存在 id 验证返回 404 ...",
      "code_snippet": "def get_profile(id):\n    user = db.query(User).filter_by(id=id).first()\n    if user is None: ...",
      "code_path": "services/profile_service.py",
      "language": "python",
      "tags": ["空指针", "AttributeError", "500"],
      "severity": "high",
      "product": "订单服务",
      "component": "profile_service",
      "source_url": "https://codehub.../merge_requests/142"
    }
  ]
}
```

CSV 首行表头：`title,root_cause,verification,code_snippet,code_path,language,tags,severity,product,component,source_url`
（`tags` 用分号或逗号分隔）

入库方式：
- Web 界面「知识库」页上传 / 粘贴
- 脚本：`./run.sh ingest your_data.json`

## 技术栈

- **后端**：FastAPI + ChromaDB + SQLite + OpenAI 兼容 LLM
- **前端**：React + Vite + TypeScript（工业等宽深色主题）
- **部署**：Docker multi-stage / 裸机脚本

## 项目结构

```
├── backend/
│   ├── app/
│   │   ├── config.py            配置 (pydantic-settings)
│   │   ├── models.py            API 契约 (请求/响应模型)
│   │   ├── main.py              FastAPI 入口 (CORS + SPA 托管)
│   │   ├── api/routes.py        路由 (12 个端点)
│   │   ├── knowledge/
│   │   │   ├── schema.py        知识库记录模型
│   │   │   └── store.py         ChromaDB + SQLite 存储
│   │   └── services/
│   │       ├── llm.py           LLM + Embedding 客户端
│   │       ├── codehub.py       CodeHub MR 客户端 (GitLab v4, mock 兜底)
│   │       ├── analyzer.py      根因分析编排器
│   │       └── best_practice.py 最佳实践探索
│   ├── scripts/ingest_kb.py     批量入库脚本
│   ├── data/samples/            样例知识库
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── src/                     分析页 / 知识库页 / 设置页
├── Dockerfile                   多阶段构建
├── docker-compose.yml
└── run.sh                       本地运行脚本
```

import type {
  AnalyzeRequest,
  AnalyzeResponse,
  HealthResponse,
  IngestResult,
  KnowledgeListResponse,
  KnowledgeRecordIn,
  KnowledgeSearchResponse,
  KnowledgeStats,
  TestResult,
} from './types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = ''
    try {
      const data = (await res.json()) as Record<string, unknown>
      detail =
        (data && typeof data === 'object' && (String(data.detail) || String(data.message))) ||
        JSON.stringify(data)
    } catch {
      detail = await res.text().catch(() => '')
    }
    throw new ApiError(res.status, detail || res.statusText || '请求失败')
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function jsonInit(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  // ---- 分析 ----
  analyze(req: AnalyzeRequest): Promise<AnalyzeResponse> {
    return fetch(`${BASE}/analyze`, jsonInit(req)).then(handle<AnalyzeResponse>)
  },

  // ---- 知识库 ----
  searchKnowledge(query: string, top_k = 5): Promise<KnowledgeSearchResponse> {
    return fetch(`${BASE}/knowledge/search`, jsonInit({ query, top_k })).then(
      handle<KnowledgeSearchResponse>,
    )
  },

  ingestKnowledge(records: KnowledgeRecordIn[]): Promise<IngestResult> {
    return fetch(`${BASE}/knowledge/ingest`, jsonInit({ records })).then(handle<IngestResult>)
  },

  uploadKnowledge(file: File): Promise<IngestResult> {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/knowledge/upload`, { method: 'POST', body: form }).then(
      handle<IngestResult>,
    )
  },

  knowledgeStats(): Promise<KnowledgeStats> {
    return fetch(`${BASE}/knowledge/stats`).then(handle<KnowledgeStats>)
  },

  knowledgeList(limit = 20, offset = 0): Promise<KnowledgeListResponse> {
    return fetch(`${BASE}/knowledge/list?limit=${limit}&offset=${offset}`).then(
      handle<KnowledgeListResponse>,
    )
  },

  deleteKnowledge(id: string): Promise<{ ok: boolean }> {
    return fetch(`${BASE}/knowledge/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }).then(handle<{ ok: boolean }>)
  },

  clearKnowledge(): Promise<{ ok: boolean }> {
    return fetch(`${BASE}/knowledge?confirm=true`, { method: 'DELETE' }).then(
      handle<{ ok: boolean }>,
    )
  },

  // ---- 健康 / 设置 ----
  health(): Promise<HealthResponse> {
    return fetch(`${BASE}/health`).then(handle<HealthResponse>)
  },

  testLLM(): Promise<TestResult> {
    return fetch(`${BASE}/settings/test/llm`, { method: 'POST' }).then(handle<TestResult>)
  },

  testCodeHub(): Promise<TestResult> {
    return fetch(`${BASE}/settings/test/codehub`, { method: 'POST' }).then(handle<TestResult>)
  },

  testEmbedding(): Promise<TestResult> {
    return fetch(`${BASE}/settings/test/embedding`, { method: 'POST' }).then(handle<TestResult>)
  },
}

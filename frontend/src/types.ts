// ====== 分析 API ======
export type Depth = 'quick' | 'standard' | 'deep'
export type AnalyzeStatus = 'ok' | 'partial' | 'error'

export interface AnalyzeRequest {
  mr_url?: string
  repo?: string
  branch?: string
  ticket_url?: string
  pasted_content?: string
  depth: Depth
}

export interface CodeRef {
  file: string
  lines?: string | null
  snippet?: string | null
  explanation?: string | null
}

export interface RootCause {
  summary: string
  category?: string | null
  contributing_factors: string[]
  evidence: CodeRef[]
  severity?: string | null
}

export interface MatchedCase {
  id: string
  title: string
  root_cause?: string | null
  verification?: string | null
  code_snippet?: string | null
  code_path?: string | null
  language?: string | null
  tags: string[]
  similarity: number
  source_url?: string | null
}

export interface BestPractice {
  title: string
  description: string
  source?: string | null
  applicability?: string | null
}

export interface CodeChange {
  file: string
  change_type?: string | null
  description: string
  patch?: string | null
}

export interface DesignSolution {
  approach: string
  rationale: string
  code_changes: CodeChange[]
  tradeoffs: string[]
  prevention: string[]
}

export interface VerificationSuggestion {
  steps: string[]
  test_cases: string[]
  risks: string[]
}

export interface MRSummary {
  mr_iid?: string | null
  title?: string | null
  source_branch?: string | null
  target_branch?: string | null
  author?: string | null
  state?: string | null
  changed_files: number
  description?: string | null
  diff_stats?: Record<string, unknown> | null
  changed_file_paths: string[]
  diff?: string | null
}

export interface AnalyzeResponse {
  task_id: string
  status: AnalyzeStatus
  warnings: string[]
  mr?: MRSummary | null
  root_cause?: RootCause | null
  matched_cases: MatchedCase[]
  best_practices: BestPractice[]
  design_solution?: DesignSolution | null
  verification?: VerificationSuggestion | null
  elapsed_ms?: number | null
  created_at: string
}

// ====== 知识库 API ======
export interface KnowledgeSearchResponse {
  query: string
  results: MatchedCase[]
}

export interface IngestResult {
  ingested: number
  skipped: number
  errors: string[]
}

export interface KnowledgeStats {
  total: number
  last_updated?: string | null
  embed_provider?: string | null
}

export interface KnowledgeRecordIn {
  title: string
  summary?: string
  root_cause: string
  verification?: string
  code_snippet?: string
  code_path?: string
  language?: string
  tags?: string[]
  severity?: string
  product?: string
  component?: string
  source_url?: string
  raw?: string
}

export interface KnowledgeRecord extends KnowledgeRecordIn {
  id: string
  created_at?: string | null
}

export interface KnowledgeListResponse {
  total: number
  items: KnowledgeRecord[]
}

// ====== 健康 / 设置 ======
export interface HealthResponse {
  status: string
  llm_configured: boolean
  codehub_configured: boolean
  codehub_mock: boolean
  embed_provider: string
  kb_count: number
  version: string
}

export interface TestResult {
  ok: boolean
  message: string
  detail?: string | null
}

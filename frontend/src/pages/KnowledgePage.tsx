import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api'
import type { IngestResult, KnowledgeRecord, KnowledgeStats, MatchedCase } from '../types'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { CodeBlock } from '../components/CodeBlock'
import { SimilarityBar } from '../components/SimilarityBar'
import {
  ErrorBanner,
  EmptyState,
  Loader,
  SuccessBanner,
  Tag,
} from '../components/Feedback'

const PAGE_SIZE = 20

const FORMAT_EXAMPLE = `[
  {
    "title": "内存泄漏：连接池未在 finally 中关闭",
    "summary": "高并发下连接池对象未释放导致 OOM",
    "root_cause": "HttpExecutor 未在异常路径关闭连接",
    "verification": "压测 1h 后堆中 HttpURLConnection 实例数稳定",
    "code_snippet": "finally { pool.close(); }",
    "code_path": "src/HttpExecutor.java",
    "language": "java",
    "tags": ["memory", "connection-pool"],
    "severity": "high",
    "product": "gateway",
    "component": "executor",
    "source_url": "https://codehub.../issues/12"
  }
]`

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return `[${e.status}] ${e.message}`
  if (e instanceof Error) return e.message
  return String(e)
}

export function KnowledgePage() {
  // stats
  const [stats, setStats] = useState<KnowledgeStats | null>(null)
  const [statsErr, setStatsErr] = useState<string | null>(null)

  // search
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<MatchedCase[] | null>(null)
  const [searchErr, setSearchErr] = useState<string | null>(null)

  // ingest
  const [ingestText, setIngestText] = useState('')
  const [ingesting, setIngesting] = useState(false)
  const [ingestRes, setIngestRes] = useState<IngestResult | null>(null)
  const [ingestErr, setIngestErr] = useState<string | null>(null)

  // upload
  const [uploading, setUploading] = useState(false)
  const [uploadRes, setUploadRes] = useState<IngestResult | null>(null)
  const [uploadErr, setUploadErr] = useState<string | null>(null)

  // list
  const [items, setItems] = useState<KnowledgeRecord[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [listLoading, setListLoading] = useState(false)
  const [listErr, setListErr] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const refreshStats = useCallback(async () => {
    try {
      const s = await api.knowledgeStats()
      setStats(s)
      setStatsErr(null)
    } catch (e) {
      setStatsErr(errMsg(e))
    }
  }, [])

  const refreshList = useCallback(async (off: number) => {
    setListLoading(true)
    setListErr(null)
    try {
      const r = await api.knowledgeList(PAGE_SIZE, off)
      setItems(r.items)
      setTotal(r.total)
      setOffset(off)
    } catch (e) {
      setListErr(errMsg(e))
      setItems([])
    } finally {
      setListLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshStats()
    void refreshList(0)
  }, [refreshStats, refreshList])

  async function handleSearch() {
    if (!query.trim()) return
    setSearching(true)
    setSearchErr(null)
    setResults(null)
    try {
      const r = await api.searchKnowledge(query.trim(), 5)
      setResults(r.results)
    } catch (e) {
      setSearchErr(errMsg(e))
    } finally {
      setSearching(false)
    }
  }

  async function handleIngest() {
    if (!ingestText.trim()) return
    setIngesting(true)
    setIngestErr(null)
    setIngestRes(null)
    try {
      const records = JSON.parse(ingestText) as unknown
      const arr = Array.isArray(records)
        ? records
        : (records as { records?: unknown[] }).records
      if (!Array.isArray(arr)) {
        throw new Error('JSON 应为数组或 {records:[...]} 对象')
      }
      const res = await api.ingestKnowledge(arr as never[])
      setIngestRes(res)
      setIngestText('')
      void refreshStats()
      void refreshList(0)
    } catch (e) {
      setIngestErr(errMsg(e))
    } finally {
      setIngesting(false)
    }
  }

  async function handleUpload(file: File | null) {
    if (!file) return
    setUploading(true)
    setUploadErr(null)
    setUploadRes(null)
    try {
      const res = await api.uploadKnowledge(file)
      setUploadRes(res)
      void refreshStats()
      void refreshList(0)
    } catch (e) {
      setUploadErr(errMsg(e))
    } finally {
      setUploading(false)
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id)
    try {
      await api.deleteKnowledge(id)
      void refreshStats()
      void refreshList(offset)
    } catch (e) {
      setListErr(errMsg(e))
    } finally {
      setDeletingId(null)
    }
  }

  async function handleClear() {
    if (!window.confirm('确认清空整个知识库？此操作不可恢复。')) return
    try {
      await api.clearKnowledge()
      void refreshStats()
      void refreshList(0)
    } catch (e) {
      setListErr(errMsg(e))
    }
  }

  return (
    <div>
      <div className="page__head">
        <div>
          <h1 className="page__title">知识库</h1>
          <p className="page__desc">历史问题案例的检索、入库与管理</p>
        </div>
        <button type="button" className="btn btn--danger btn--sm" onClick={handleClear}>
          清空知识库
        </button>
      </div>

      {/* stats */}
      <div className="statgrid">
        <div className="stat">
          <div className="stat__k">记录总数</div>
          <div className="stat__v">{stats?.total ?? '—'}</div>
        </div>
        <div className="stat">
          <div className="stat__k">向量提供方</div>
          <div className="stat__v" style={{ fontSize: 14 }}>{stats?.embed_provider ?? '—'}</div>
        </div>
        <div className="stat">
          <div className="stat__k">最近更新</div>
          <div className="stat__v" style={{ fontSize: 13 }}>{stats?.last_updated ?? '—'}</div>
        </div>
      </div>
      {statsErr && <ErrorBanner message={statsErr} />}

      {/* search */}
      <Card title="语义检索">
        <div className="searchbar">
          <input
            className="input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleSearch()
            }}
            placeholder="输入查询文本，如「连接池内存泄漏」"
          />
          <button
            type="button"
            className="btn btn--primary"
            disabled={searching || !query.trim()}
            onClick={handleSearch}
          >
            {searching ? '检索中…' : '搜索'}
          </button>
        </div>

        {searching && <Loader label="正在检索相似案例…" />}
        {searchErr && <ErrorBanner message={searchErr} />}
        {results && (
          <div className="stack">
            {results.length === 0 ? (
              <EmptyState text="未找到匹配案例" />
            ) : (
              results.map((c) => (
                <article key={c.id} className="case">
                  <div className="case__top">
                    <h3 className="case__title">{c.title}</h3>
                    <SimilarityBar value={c.similarity} />
                  </div>
                  {c.root_cause && (
                    <p className="prose">
                      <span className="label">根因：</span>
                      {c.root_cause}
                    </p>
                  )}
                  {c.verification && (
                    <p className="prose prose--muted">
                      <span className="label">验证：</span>
                      {c.verification}
                    </p>
                  )}
                  {(c.code_snippet || c.code_path) && (
                    <CodeBlock code={c.code_snippet} title={c.code_path} language={c.language ?? undefined} />
                  )}
                  <div className="case__foot">
                    <div className="chiprow">
                      {c.tags.map((t, i) => (
                        <Tag key={i}>{t}</Tag>
                      ))}
                    </div>
                    {c.source_url && (
                      <a className="link" href={c.source_url} target="_blank" rel="noreferrer">
                        来源 ↗
                      </a>
                    )}
                  </div>
                </article>
              ))
            )}
          </div>
        )}
      </Card>

      {/* ingest */}
      <Card title="入库">
        <div className="stack">
          <div className="field">
            <label className="field__label">上传文件（.json / .csv）</label>
            <input
              className="fileinput"
              type="file"
              accept=".json,.csv"
              disabled={uploading}
              onChange={(e) => void handleUpload(e.target.files?.[0] ?? null)}
            />
          </div>
          {uploading && <Loader label="上传解析中…" />}
          {uploadErr && <ErrorBanner message={uploadErr} />}
          {uploadRes && (
            <SuccessBanner
              message={`入库 ${uploadRes.ingested} 条，跳过 ${uploadRes.skipped} 条${
                uploadRes.errors.length ? `，错误 ${uploadRes.errors.length}` : ''
              }`}
            />
          )}

          <div className="divider-y" />

          <div className="field">
            <label className="field__label">粘贴 JSON 入库</label>
            <textarea
              className="textarea"
              style={{ minHeight: 160 }}
              value={ingestText}
              onChange={(e) => setIngestText(e.target.value)}
              placeholder={FORMAT_EXAMPLE}
            />
            <span className="field__hint">
              支持数组 <code>[...]</code> 或 <code>{'{records:[...]}'}</code> 两种形式
            </span>
          </div>
          <div className="field field--row">
            <button
              type="button"
              className="btn btn--primary"
              disabled={ingesting || !ingestText.trim()}
              onClick={handleIngest}
            >
              {ingesting ? '入库中…' : '入库 ▸'}
            </button>
          </div>
          {ingestErr && <ErrorBanner message={ingestErr} />}
          {ingestRes && (
            <SuccessBanner
              message={`入库 ${ingestRes.ingested} 条，跳过 ${ingestRes.skipped} 条${
                ingestRes.errors.length ? `，错误 ${ingestRes.errors.length}` : ''
              }`}
            />
          )}
        </div>
      </Card>

      {/* format hint */}
      <Card title="JSON 格式示例（KnowledgeRecordIn）">
        <CodeBlock code={FORMAT_EXAMPLE} language="json" />
      </Card>

      {/* list */}
      <Card
        title="记录列表"
        right={<Badge tone="gray">共 {total} 条</Badge>}
      >
        {listLoading && <Loader label="加载记录…" />}
        {listErr && <ErrorBanner message={listErr} />}
        {!listLoading && items.length === 0 && !listErr && (
          <EmptyState text="知识库为空，请先入库记录" />
        )}
        <div className="stack">
          {items.map((r) => (
            <article key={r.id} className="recrow">
              <div className="recrow__top">
                <span className="recrow__title">{r.title}</span>
                {r.severity && <Badge tone={r.severity.toLowerCase() === 'high' ? 'red' : 'yellow'}>{r.severity}</Badge>}
              </div>
              {r.root_cause && <div className="recrow__body">{r.root_cause}</div>}
              <div className="recrow__foot">
                <div className="chiprow" style={{ margin: 0 }}>
                  {(r.tags ?? []).map((t, i) => (
                    <Tag key={i}>{t}</Tag>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn btn--danger btn--sm"
                  disabled={deletingId === r.id}
                  onClick={() => void handleDelete(r.id)}
                >
                  {deletingId === r.id ? '删除中…' : '删除'}
                </button>
              </div>
            </article>
          ))}
        </div>

        {total > PAGE_SIZE && (
          <div className="pager">
            <button
              type="button"
              className="btn btn--sm"
              disabled={offset === 0 || listLoading}
              onClick={() => void refreshList(Math.max(0, offset - PAGE_SIZE))}
            >
              ◂ 上一页
            </button>
            <span className="pager__info">
              {offset + 1} – {Math.min(offset + PAGE_SIZE, total)} / {total}
            </span>
            <button
              type="button"
              className="btn btn--sm"
              disabled={offset + PAGE_SIZE >= total || listLoading}
              onClick={() => void refreshList(offset + PAGE_SIZE)}
            >
              下一页 ▸
            </button>
          </div>
        )}
      </Card>
    </div>
  )
}

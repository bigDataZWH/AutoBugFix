import { useState } from 'react'
import { ApiError, api } from '../api'
import type { AnalyzeResponse, Depth } from '../types'
import { Card } from '../components/Card'
import { ErrorBanner, Loader } from '../components/Feedback'
import { AnalysisResultView } from '../components/AnalysisResultView'

const DEPTHS: { id: Depth; label: string; desc: string }[] = [
  { id: 'quick', label: 'QUICK', desc: '快速' },
  { id: 'standard', label: 'STANDARD', desc: '标准' },
  { id: 'deep', label: 'DEEP', desc: '深度' },
]

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return `[${e.status}] ${e.message}`
  if (e instanceof Error) return e.message
  return String(e)
}

export function AnalysisPage() {
  const [mrUrl, setMrUrl] = useState('')
  const [repo, setRepo] = useState('')
  const [branch, setBranch] = useState('')
  const [ticketUrl, setTicketUrl] = useState('')
  const [pasted, setPasted] = useState('')
  const [depth, setDepth] = useState<Depth>('standard')
  const [showPaste, setShowPaste] = useState(false)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)

  const canSubmit = !loading && (!!mrUrl.trim() || !!pasted.trim())

  async function handleSubmit() {
    if (!canSubmit) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.analyze({
        mr_url: mrUrl.trim() || undefined,
        repo: repo.trim() || undefined,
        branch: branch.trim() || undefined,
        ticket_url: ticketUrl.trim() || undefined,
        pasted_content: pasted.trim() || undefined,
        depth,
      })
      setResult(res)
    } catch (e) {
      setError(errMsg(e))
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="page__head">
        <div>
          <h1 className="page__title">分析</h1>
          <p className="page__desc">输入 MR / 问题单，自动定位根因并给出解决方案</p>
        </div>
      </div>

      <Card title="分析输入">
        <div className="form">
          <div className="field">
            <label className="field__label">MR 链接</label>
            <input
              className="input"
              value={mrUrl}
              onChange={(e) => setMrUrl(e.target.value)}
              placeholder="https://codehub.../merge_requests/123"
            />
          </div>

          <div className="kvgrid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div className="field">
              <label className="field__label">仓库</label>
              <input
                className="input"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="namespace/project"
              />
            </div>
            <div className="field">
              <label className="field__label">分支</label>
              <input
                className="input"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder="master"
              />
            </div>
          </div>

          <div className="field">
            <label className="field__label">问题单链接（可选）</label>
            <input
              className="input"
              value={ticketUrl}
              onChange={(e) => setTicketUrl(e.target.value)}
              placeholder="https://issues.../tickets/456"
            />
          </div>

          <div className="field">
            <label className="field__label">分析深度</label>
            <div className="segmented">
              {DEPTHS.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  className={`segmented__opt${depth === d.id ? ' segmented__opt--active' : ''}`}
                  onClick={() => setDepth(d.id)}
                  title={d.desc}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <button
              type="button"
              className="collapsible__toggle"
              onClick={() => setShowPaste((s) => !s)}
            >
              {showPaste ? '▼' : '▶'} 无 CodeHub 时直接粘贴内容
            </button>
            {showPaste && (
              <textarea
                className="textarea"
                style={{ minHeight: 140, marginTop: 8 }}
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
                placeholder="直接粘贴 MR / 问题单 / diff 文本…"
              />
            )}
          </div>

          <div className="field field--row">
            <button
              type="button"
              className="btn btn--primary"
              disabled={!canSubmit}
              onClick={handleSubmit}
            >
              {loading ? '分析中…' : '开始分析 ▸'}
            </button>
            {result && (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setResult(null)
                  setError(null)
                }}
              >
                清除结果
              </button>
            )}
          </div>
        </div>
      </Card>

      {loading && (
        <Card>
          <Loader label="正在分析 MR / 根因 / 历史案例 …" />
        </Card>
      )}

      {error && <ErrorBanner message={error} />}

      {result && !loading && <AnalysisResultView data={result} />}
    </div>
  )
}

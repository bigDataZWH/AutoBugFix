import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api'
import type { HealthResponse, TestResult } from '../types'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { ErrorBanner, KeyValue, Loader } from '../components/Feedback'

type TestKey = 'llm' | 'codehub' | 'embedding'

interface TestState {
  loading: boolean
  result: TestResult | null
}

const TEST_META: { key: TestKey; name: string }[] = [
  { key: 'llm', name: 'LLM 大模型' },
  { key: 'codehub', name: 'CodeHub 代码仓' },
  { key: 'embedding', name: 'Embedding 向量' },
]

interface EnvVar {
  key: string
  desc: string
}

const ENV_VARS: EnvVar[] = [
  { key: 'LLM_BASE_URL', desc: '大模型网关 (OpenAI 兼容)，如 http://localhost:11434/v1' },
  { key: 'LLM_API_KEY', desc: '大模型密钥；Ollama 填 ollama' },
  { key: 'LLM_MODEL', desc: '模型名，如 qwen2.5:7b' },
  { key: 'EMBED_PROVIDER', desc: 'api(走 OpenAI 兼容 /embeddings) | local(本地 sentence-transformers)' },
  { key: 'EMBED_BASE_URL', desc: '向量网关，留空则复用 LLM_BASE_URL' },
  { key: 'EMBED_API_KEY', desc: '向量密钥，留空则复用 LLM_API_KEY' },
  { key: 'CODEHUB_BASE_URL', desc: 'CodeHub 地址，GitLab v4 兼容协议' },
  { key: 'CODEHUB_TOKEN', desc: '私有 token；留空且 MOCK=true 时用内置样例' },
  { key: 'CODEHUB_MOCK', desc: 'true 时使用内置样例 MR，便于离线体验' },
  { key: 'WEB_SEARCH_PROVIDER', desc: 'none | ddgs(免费) | tavily(需 key)，用于联网最佳实践' },
]

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return `[${e.status}] ${e.message}`
  if (e instanceof Error) return e.message
  return String(e)
}

export function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthErr, setHealthErr] = useState<string | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)

  const [tests, setTests] = useState<Record<TestKey, TestState>>({
    llm: { loading: false, result: null },
    codehub: { loading: false, result: null },
    embedding: { loading: false, result: null },
  })

  const refreshHealth = useCallback(async () => {
    setHealthLoading(true)
    setHealthErr(null)
    try {
      const h = await api.health()
      setHealth(h)
    } catch (e) {
      setHealthErr(errMsg(e))
    } finally {
      setHealthLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshHealth()
  }, [refreshHealth])

  async function runTest(key: TestKey) {
    setTests((prev) => ({ ...prev, [key]: { loading: true, result: null } }))
    try {
      const fn =
        key === 'llm' ? api.testLLM : key === 'codehub' ? api.testCodeHub : api.testEmbedding
      const result = await fn()
      setTests((prev) => ({ ...prev, [key]: { loading: false, result } }))
    } catch (e) {
      setTests((prev) => ({
        ...prev,
        [key]: {
          loading: false,
          result: { ok: false, message: errMsg(e), detail: null },
        },
      }))
    }
  }

  return (
    <div>
      <div className="page__head">
        <div>
          <h1 className="page__title">设置</h1>
          <p className="page__desc">后端连接测试、健康状态与配置说明</p>
        </div>
        <button type="button" className="btn btn--sm" onClick={() => void refreshHealth()}>
          刷新健康状态
        </button>
      </div>

      <Card title="连接测试">
        <div className="testgrid">
          {TEST_META.map((t) => {
            const st = tests[t.key]
            return (
              <div key={t.key} className="testcard">
                <div className="testcard__head">
                  <span className="testcard__name">{t.name}</span>
                  {st.result && (
                    <Badge tone={st.result.ok ? 'green' : 'red'}>
                      {st.result.ok ? 'OK' : 'FAIL'}
                    </Badge>
                  )}
                </div>
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={st.loading}
                  onClick={() => void runTest(t.key)}
                >
                  {st.loading ? '测试中…' : '运行测试'}
                </button>
                {st.result && (
                  <>
                    <div className="testcard__msg">{st.result.message}</div>
                    {st.result.detail && <div className="testcard__detail">{st.result.detail}</div>}
                  </>
                )}
              </div>
            )
          })}
        </div>
      </Card>

      <Card
        title="健康状态"
        right={
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void refreshHealth()}>
            刷新
          </button>
        }
      >
        {healthLoading && <Loader label="获取健康状态…" />}
        {healthErr && <ErrorBanner message={healthErr} />}
        {health && (
          <div className="healthgrid">
            <KeyValue k="status" v={<Badge tone={health.status === 'ok' ? 'green' : 'yellow'}>{health.status}</Badge>} />
            <KeyValue k="version" v={health.version} />
            <KeyValue k="kb_count" v={health.kb_count} />
            <KeyValue k="embed_provider" v={health.embed_provider} />
            <KeyValue k="llm_configured" v={<Badge tone={health.llm_configured ? 'green' : 'red'}>{String(health.llm_configured)}</Badge>} />
            <KeyValue k="codehub_configured" v={<Badge tone={health.codehub_configured ? 'green' : 'red'}>{String(health.codehub_configured)}</Badge>} />
            <KeyValue k="codehub_mock" v={<Badge tone={health.codehub_mock ? 'yellow' : 'gray'}>{String(health.codehub_mock)}</Badge>} />
          </div>
        )}
      </Card>

      <Card title="配置说明">
        <p className="prose prose--muted" style={{ marginBottom: 12 }}>
          编辑 <code style={{ color: 'var(--accent)' }}>backend/.env</code> 修改配置后重启后端生效。
        </p>
        <table className="envtable">
          <thead>
            <tr>
              <th>环境变量</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {ENV_VARS.map((v) => (
              <tr key={v.key}>
                <td>
                  <code>{v.key}</code>
                </td>
                <td>{v.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

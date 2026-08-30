import type { AnalyzeResponse, MRSummary, RootCause } from '../types'
import { Badge, SeverityBadge, StatusBadge } from './Badge'
import { Card } from './Card'
import { CodeBlock } from './CodeBlock'
import { SimilarityBar } from './SimilarityBar'
import { EmptyState, KeyValue, StringList, Tag } from './Feedback'

function DiffStats({ stats }: { stats?: Record<string, unknown> | null }) {
  if (!stats) return null
  const entries = Object.entries(stats).filter(([, v]) => v != null && v !== '')
  if (entries.length === 0) return null
  return (
    <div className="kvgrid">
      {entries.map(([k, v]) => (
        <KeyValue key={k} k={k} v={String(v)} />
      ))}
    </div>
  )
}

function MRSummaryCard({ mr }: { mr: MRSummary }) {
  return (
    <Card
      title="MR 概要"
      right={
        mr.state ? <Badge tone="blue">{mr.state}</Badge> : null
      }
    >
      <div className="kvgrid">
        <KeyValue k="MR IID" v={mr.mr_iid} />
        <KeyValue k="标题" v={mr.title} />
        <KeyValue k="分支" v={`${mr.source_branch ?? '?'} → ${mr.target_branch ?? '?'}`} />
        <KeyValue k="作者" v={mr.author} />
        <KeyValue k="变更文件数" v={mr.changed_files} />
      </div>
      {mr.description && <p className="prose">{mr.description}</p>}
      <DiffStats stats={mr.diff_stats} />
      {mr.changed_file_paths.length > 0 && (
        <div className="chiprow">
          {mr.changed_file_paths.map((p, i) => (
            <span key={i} className="chip">{p}</span>
          ))}
        </div>
      )}
      {mr.diff && mr.diff.trim() && (
        <CodeBlock code={mr.diff} title="diff" language="diff" />
      )}
    </Card>
  )
}

function RootCauseCard({ rc }: { rc: RootCause }) {
  return (
    <Card
      title="根因分析"
      right={
        <div className="badgerow">
          {rc.category && <Badge tone="purple">CAT · {rc.category}</Badge>}
          <SeverityBadge severity={rc.severity} />
        </div>
      }
    >
      <p className="prose">{rc.summary}</p>
      <h3 className="subhead">贡献因素</h3>
      <StringList items={rc.contributing_factors} tone="yellow" />
      {rc.evidence.length > 0 && (
        <>
          <h3 className="subhead">证据</h3>
          <div className="stack">
            {rc.evidence.map((ev, i) => (
              <div key={i} className="evidence">
                <div className="badgerow">
                  <Badge tone="blue">{ev.file}</Badge>
                  {ev.lines && <Badge tone="gray">L {ev.lines}</Badge>}
                </div>
                {ev.explanation && <p className="prose prose--muted">{ev.explanation}</p>}
                <CodeBlock code={ev.snippet} />
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

function MatchedCasesSection({ cases }: { cases: AnalyzeResponse['matched_cases'] }) {
  return (
    <Card title="匹配的历史案例" subtitle={`共 ${cases.length} 条`}>
      {cases.length === 0 ? (
        <EmptyState text="未匹配到历史案例" />
      ) : (
        <div className="stack">
          {cases.map((c) => (
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
                <p className="prose">
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
          ))}
        </div>
      )}
    </Card>
  )
}

function BestPracticesSection({ items }: { items: AnalyzeResponse['best_practices'] }) {
  return (
    <Card title="最佳实践" subtitle={`共 ${items.length} 条`}>
      {items.length === 0 ? (
        <EmptyState text="无最佳实践" />
      ) : (
        <div className="stack">
          {items.map((bp, i) => (
            <article key={i} className="bp">
              <h3 className="bp__title">{bp.title}</h3>
              <p className="prose">{bp.description}</p>
              <div className="kvgrid">
                <KeyValue k="来源" v={bp.source} />
                <KeyValue k="适用性" v={bp.applicability} />
              </div>
            </article>
          ))}
        </div>
      )}
    </Card>
  )
}

function DesignSolutionCard({ data }: { data: NonNullable<AnalyzeResponse['design_solution']> }) {
  return (
    <Card title="设计方案">
      <h3 className="subhead">方案</h3>
      <p className="prose">{data.approach}</p>
      <h3 className="subhead">理由</h3>
      <p className="prose">{data.rationale}</p>
      {data.code_changes.length > 0 && (
        <>
          <h3 className="subhead">代码变更</h3>
          <div className="stack">
            {data.code_changes.map((cc, i) => (
              <div key={i} className="codechange">
                <div className="badgerow">
                  <Badge tone="blue">{cc.file}</Badge>
                  {cc.change_type && <Badge tone="purple">{cc.change_type}</Badge>}
                </div>
                <p className="prose prose--muted">{cc.description}</p>
                <CodeBlock code={cc.patch} title="patch" language="diff" />
              </div>
            ))}
          </div>
        </>
      )}
      <h3 className="subhead">权衡</h3>
      <StringList items={data.tradeoffs} tone="yellow" />
      <h3 className="subhead">预防</h3>
      <StringList items={data.prevention} tone="green" />
    </Card>
  )
}

function VerificationCard({ v }: { v: NonNullable<AnalyzeResponse['verification']> }) {
  return (
    <Card title="验证建议">
      <h3 className="subhead">步骤</h3>
      <StringList items={v.steps} tone="blue" />
      <h3 className="subhead">测试用例</h3>
      <StringList items={v.test_cases} tone="green" />
      <h3 className="subhead">风险</h3>
      <StringList items={v.risks} tone="red" />
    </Card>
  )
}

export function AnalysisResultView({ data }: { data: AnalyzeResponse }) {
  const { mr, root_cause, matched_cases, best_practices, design_solution, verification } = data
  return (
    <div className="result">
      <div className={`resultbar resultbar--${data.status}`}>
        <div className="resultbar__left">
          <StatusBadge status={data.status} />
          <span className="resultbar__meta">TASK {data.task_id.slice(0, 8)}</span>
          {data.elapsed_ms != null && (
            <span className="resultbar__meta">{data.elapsed_ms} ms</span>
          )}
          {data.created_at && <span className="resultbar__meta">{data.created_at}</span>}
        </div>
        {data.warnings.length > 0 && (
          <span className="resultbar__warn">⚠ {data.warnings.length} 警告</span>
        )}
      </div>

      {data.warnings.length > 0 && (
        <ul className="warnlist">
          {data.warnings.map((w, i) => (
            <li key={i} className="warnlist__item">{w}</li>
          ))}
        </ul>
      )}

      <div className="result__grid">
        {mr && <MRSummaryCard mr={mr} />}
        {root_cause && <RootCauseCard rc={root_cause} />}
        <MatchedCasesSection cases={matched_cases} />
        <BestPracticesSection items={best_practices} />
        {design_solution && <DesignSolutionCard data={design_solution} />}
        {verification && <VerificationCard v={verification} />}
      </div>
    </div>
  )
}

import type { ReactNode } from 'react'

export function Loader({ label = '处理中…' }: { label?: string }) {
  return (
    <div className="loader">
      <span className="loader__spinner" aria-hidden />
      <span className="loader__text">{label}</span>
    </div>
  )
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="banner banner--error">
      <span className="banner__tag">ERR</span>
      <span className="banner__msg">{message}</span>
    </div>
  )
}

export function SuccessBanner({ message }: { message: string }) {
  return (
    <div className="banner banner--success">
      <span className="banner__tag">OK</span>
      <span className="banner__msg">{message}</span>
    </div>
  )
}

export function EmptyState({ text }: { text: string }) {
  return <div className="empty">{text}</div>
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="tag">{children}</span>
}

function display(v: ReactNode): ReactNode {
  if (v === undefined || v === null || v === '') return '—'
  return v
}

export function KeyValue({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <span className="kv__k">{k}</span>
      <span className="kv__v">{display(v)}</span>
    </div>
  )
}

type ListTone = 'blue' | 'green' | 'yellow' | 'red' | 'gray'

export function StringList({ items, tone = 'gray' }: { items: string[]; tone?: ListTone }) {
  if (!items || items.length === 0) return <EmptyState text="无" />
  return (
    <ul className={`list list--${tone}`}>
      {items.map((it, i) => (
        <li key={i} className="list__item">
          {it}
        </li>
      ))}
    </ul>
  )
}

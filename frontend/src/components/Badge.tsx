import type { ReactNode } from 'react'

export type Tone = 'green' | 'yellow' | 'red' | 'blue' | 'purple' | 'gray'

interface BadgeProps {
  tone?: Tone
  children: ReactNode
}

export function Badge({ tone = 'gray', children }: BadgeProps) {
  return <span className={`badge badge--${tone}`}>{children}</span>
}

const STATUS_TONE: Record<string, Tone> = {
  ok: 'green',
  partial: 'yellow',
  error: 'red',
}

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? 'gray'
  return <Badge tone={tone}>{status.toUpperCase()}</Badge>
}

const SEVERITY_TONE: Record<string, Tone> = {
  high: 'red',
  medium: 'yellow',
  low: 'green',
  critical: 'red',
}

export function SeverityBadge({ severity }: { severity?: string | null }) {
  if (!severity) return null
  const tone = SEVERITY_TONE[String(severity).toLowerCase()] ?? 'gray'
  return <Badge tone={tone}>SEV · {severity}</Badge>
}

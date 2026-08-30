import type { ReactNode } from 'react'

interface CardProps {
  title?: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
}

export function Card({ title, subtitle, right, children, className }: CardProps) {
  return (
    <section className={`card ${className ?? ''}`.trim()}>
      {(title || right) && (
        <header className="card__head">
          <div className="card__titles">
            {title != null && title !== '' && <h2 className="card__title">{title}</h2>}
            {subtitle != null && subtitle !== '' && (
              <p className="card__subtitle">{subtitle}</p>
            )}
          </div>
          {right != null && right !== '' && <div className="card__right">{right}</div>}
        </header>
      )}
      <div className="card__body">{children}</div>
    </section>
  )
}

import type { ReactNode } from 'react'

interface CodeBlockProps {
  code?: string | null
  language?: string
  title?: ReactNode
}

export function CodeBlock({ code, language, title }: CodeBlockProps) {
  const text = code ?? ''
  const hasHead = (title != null && title !== '') || !!language
  return (
    <div className="codeblock">
      {hasHead && (
        <div className="codeblock__head">
          <span className="codeblock__title">{title ?? ''}</span>
          {language && <span className="codeblock__lang">{language}</span>}
        </div>
      )}
      {text.trim() ? (
        <pre className="codeblock__pre">
          <code>{text}</code>
        </pre>
      ) : (
        <div className="codeblock__empty">— 无代码内容 —</div>
      )}
    </div>
  )
}

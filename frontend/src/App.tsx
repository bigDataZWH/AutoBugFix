import { useState } from 'react'
import './index.css'
import { AnalysisPage } from './pages/AnalysisPage'
import { KnowledgePage } from './pages/KnowledgePage'
import { SettingsPage } from './pages/SettingsPage'

type View = 'analysis' | 'knowledge' | 'settings'

interface NavItem {
  id: View
  label: string
  icon: string
}

const NAV: NavItem[] = [
  { id: 'analysis', label: '分析', icon: '◆' },
  { id: 'knowledge', label: '知识库', icon: '❖' },
  { id: 'settings', label: '设置', icon: '⚙' },
]

function App() {
  const [view, setView] = useState<View>('analysis')

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar__brand">
          <span className="sidebar__logo">▣</span>
          <span className="sidebar__name">ISSUE.RESOLVE</span>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button
              key={n.id}
              type="button"
              className={`nav__item${view === n.id ? ' nav__item--active' : ''}`}
              onClick={() => setView(n.id)}
            >
              <span className="nav__icon">{n.icon}</span>
              <span className="nav__label">{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar__footer">
          <span>problem-ticket resolver</span>
        </div>
      </aside>
      <main className="content">
        {view === 'analysis' && <AnalysisPage />}
        {view === 'knowledge' && <KnowledgePage />}
        {view === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}

export default App

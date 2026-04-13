import { useEffect, useState } from 'react'
import { BookOpen, ChevronDown, ChevronRight, FlaskConical, MessageSquare } from 'lucide-react'
import InverseOptimizationModal from '@/components/optimization/InverseOptimizationModal'
import { APP_VERSION } from '@/config/version'

interface QuestionGuidePanelProps {
  onQuestionSelect: (text: string, immediate?: boolean) => void
  optimizationGuideOpen: boolean
  onOpenOptimizationGuide: () => void
  onCloseOptimizationGuide: () => void
}

interface QNode {
  type: 'h1' | 'h2' | 'question'
  text: string
  id: string
  children?: QNode[]
}

function parseQuestionList(content: string): QNode[] {
  const lines = content.split('\n').map((l) => l.trimEnd())
  const roots: QNode[] = []
  let currentH1: QNode | null = null
  let currentH2: QNode | null = null
  let counter = 0

  for (const line of lines) {
    if (line.startsWith('# ') && !line.startsWith('## ')) {
      currentH1 = { type: 'h1', text: line.slice(2).trim(), id: `h1-${counter++}`, children: [] }
      currentH2 = null
      roots.push(currentH1)
    } else if (line.startsWith('## ')) {
      currentH2 = { type: 'h2', text: line.slice(3).trim(), id: `h2-${counter++}`, children: [] }
      if (currentH1) currentH1.children!.push(currentH2)
      else roots.push(currentH2)
    } else if (line.startsWith('* ')) {
      const q: QNode = { type: 'question', text: line.slice(2).trim(), id: `q-${counter++}` }
      if (currentH2) currentH2.children!.push(q)
      else if (currentH1) currentH1.children!.push(q)
      else roots.push(q)
    }
  }

  return roots
}

function QuestionTree({
  nodes,
  onSelect,
  depth = 0,
}: {
  nodes: QNode[]
  onSelect: (text: string, immediate?: boolean) => void
  depth?: number
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className={depth > 0 ? 'ml-2 border-l border-gray-100 pl-2' : ''}>
      {nodes.map((node) => {
        if (node.type === 'question') {
          return (
            <button
              key={node.id}
              onClick={() => onSelect(node.text, false)}
              onDoubleClick={() => onSelect(node.text, true)}
              className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs text-gray-600 hover:bg-brand-red/5 hover:text-brand-red transition-colors group select-none"
            >
              <MessageSquare className="h-3.5 w-3.5 mt-0.5 shrink-0 text-gray-300 group-hover:text-brand-red/60" />
              <span className="leading-tight">{node.text}</span>
            </button>
          )
        }

        const isCollapsed = collapsed.has(node.id)
        const isH1 = node.type === 'h1'
        return (
          <div key={node.id} className="mb-0.5">
            <button
              onClick={() => toggle(node.id)}
              className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-gray-100 ${
                isH1 ? 'font-semibold text-gray-700' : 'font-medium text-gray-600'
              }`}
            >
              {isCollapsed ? (
                <ChevronRight className="h-3 w-3 shrink-0 text-gray-400" />
              ) : (
                <ChevronDown className="h-3 w-3 shrink-0 text-gray-400" />
              )}
              <span className={isH1 ? 'text-xs uppercase tracking-wide' : 'text-xs'}>{node.text}</span>
            </button>
            {!isCollapsed && node.children && node.children.length > 0 && (
              <QuestionTree nodes={node.children} onSelect={onSelect} depth={depth + 1} />
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function QuestionGuidePanel({
  onQuestionSelect,
  optimizationGuideOpen,
  onOpenOptimizationGuide,
  onCloseOptimizationGuide,
}: QuestionGuidePanelProps) {
  const [questionNodes, setQuestionNodes] = useState<QNode[]>([])
  const [activeTab, setActiveTab] = useState<'questions' | 'optimization'>('questions')

  useEffect(() => {
    fetch('/QuestionList.md')
      .then((r) => (r.ok ? r.text() : ''))
      .then((text) => setQuestionNodes(parseQuestionList(text)))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (optimizationGuideOpen) setActiveTab('optimization')
  }, [optimizationGuideOpen])

  const selectTab = (tab: 'questions' | 'optimization') => {
    setActiveTab(tab)
    if (tab === 'optimization') onOpenOptimizationGuide()
    else onCloseOptimizationGuide()
  }

  return (
    <aside className="flex h-full flex-col bg-white border-l border-gray-200 overflow-hidden">
      <div className="shrink-0 border-b border-gray-200 bg-white px-3 py-2.5">
        <div className="mb-2 flex items-center justify-between px-1 text-xs">
          <a
            href="/documentation_user.md"
            target="_blank"
            rel="noreferrer"
            className="font-semibold text-brand-red hover:underline"
          >
            설명
          </a>
          <span className="font-medium text-gray-500">version {APP_VERSION}</span>
        </div>
        <div className="grid grid-cols-2 rounded-lg bg-gray-100 p-1">
          <button
            onClick={() => selectTab('questions')}
            className={`flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${
              activeTab === 'questions' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <BookOpen className="h-3.5 w-3.5" />
            질문 템플릿
          </button>
          <button
            onClick={() => selectTab('optimization')}
            className={`flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${
              activeTab === 'optimization' ? 'bg-white text-purple-700 shadow-sm' : 'text-gray-500 hover:text-purple-700'
            }`}
          >
            <FlaskConical className="h-3.5 w-3.5" />
            최적화
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'questions' ? (
          <div className="h-full overflow-y-auto scrollbar-thin p-3">
            {questionNodes.length === 0 ? (
              <p className="text-xs text-gray-400 px-1 py-1">QuestionList.md 없음</p>
            ) : (
              <QuestionTree nodes={questionNodes} onSelect={onQuestionSelect} />
            )}
          </div>
        ) : (
          <InverseOptimizationModal
            onClose={() => {
              onCloseOptimizationGuide()
              setActiveTab('questions')
            }}
            variant="sidebar"
          />
        )}
      </div>
    </aside>
  )
}

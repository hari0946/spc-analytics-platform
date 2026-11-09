import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { askAssistant } from '../api/analysis'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import FilterBar from '../components/FilterBar'

interface Msg { who: 'you' | 'assistant'; text: string; meta?: string; supported?: boolean }
const SUGGESTIONS = ['Give me a summary', 'Are there any out-of-control points?', 'What is the mean and standard deviation?',
  'Is the data normally distributed?', 'How many readings are there?', 'What is the defect rate?']

export default function Assistant() {
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([{ who: 'assistant', text: 'Ask me about the selected historical data. I only answer from real analysis results and I will say when I cannot.' }])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { data.sensorOptions(filters).then(setOptions).catch(() => setOptions(null)) }, [filters])

  const ask = async (text: string) => {
    if (!text.trim()) return
    setMsgs((m) => [...m, { who: 'you', text }]); setQ(''); setBusy(true)
    try {
      const r = await askAssistant(text, filters)
      setMsgs((m) => [...m, { who: 'assistant', text: r.answer, supported: r.supported, meta: r.used?.length ? `from: ${r.used.join(', ')}` : undefined }])
    } catch (e) { setMsgs((m) => [...m, { who: 'assistant', text: errorMessage(e), supported: false }]) } finally { setBusy(false) }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); void ask(q) }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">AI assistant</h1><Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        <section className="rounded-xl bg-white shadow p-4 space-y-3" data-testid="chat">
          {msgs.map((m, i) => (
            <div key={i} className={`text-sm whitespace-pre-line ${m.who === 'you' ? 'text-right' : ''}`}>
              <span className={`inline-block max-w-[90%] rounded-lg px-3 py-2 text-left ${m.who === 'you' ? 'bg-blue-600 text-white' : m.supported === false ? 'bg-amber-50 text-amber-900' : 'bg-slate-100'}`}>{m.text}</span>
              {m.meta && <div className="text-xs text-slate-400 mt-0.5">{m.meta}</div>}
            </div>
          ))}
          {busy && <p className="text-sm text-slate-400">Analysing…</p>}
        </section>
        <div className="flex flex-wrap gap-2">{SUGGESTIONS.map((s) => <button key={s} onClick={() => void ask(s)} className="rounded-full bg-white shadow px-3 py-1 text-xs">{s}</button>)}</div>
        <form onSubmit={submit} className="flex gap-2">
          <input className="flex-1 rounded border px-3 py-2 text-sm" placeholder="Ask about your data…" value={q} maxLength={500} onChange={(e) => setQ(e.target.value)} aria-label="Question" />
          <button disabled={busy} className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">Ask</button>
        </form>
      </div>
    </main>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { downloadReport } from '../api/analysis'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import FilterBar from '../components/FilterBar'

const TYPES = [['spc', 'SPC report'], ['control_chart', 'Control chart report'], ['capability', 'Capability report'], ['quality', 'Quality report'], ['statistical', 'Statistical report']]

export default function Reports() {
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [type, setType] = useState('spc')
  const [format, setFormat] = useState('pdf')
  const [parameter, setParameter] = useState('')
  const [o, setO] = useState({ chart: 'IMR', subgroup: '5', usl: '', lsl: '', target: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { data.sensorOptions(filters).then(setOptions).catch(() => setOptions(null)) }, [filters])
  const params = options?.parameters.map((p) => p.value) ?? []
  useEffect(() => { if (!parameter && params.length) setParameter(params[0]) }, [params, parameter])

  const generate = async () => {
    setBusy(true); setMsg(null)
    const n = (s: string) => (s.trim() === '' ? undefined : Number(s))
    const options_: Record<string, unknown> = { chart: o.chart, usl: n(o.usl), lsl: n(o.lsl), target: n(o.target) }
    if (['XBAR_R', 'XBAR_S', 'R', 'S'].includes(o.chart)) options_.subgroup_size = n(o.subgroup)
    try {
      await downloadReport({
        type, format, parameter: type === 'quality' ? null : parameter, upload_id: filters.upload_id, plant: filters.plant, line: filters.line,
        asset: filters.asset, tag: filters.tag, batch: filters.batch, start: filters.start || null, end: filters.end || null,
        options: Object.fromEntries(Object.entries(options_).filter(([, v]) => v !== undefined)),
      }, `spc_${type}.${format}`)
      setMsg({ ok: true, text: 'Report generated and downloaded.' })
    } catch (e) {
      const err = e as { response?: { data?: Blob } }
      let text = errorMessage(e)
      if (err.response?.data instanceof Blob) { try { text = JSON.parse(await err.response.data.text()).error.message } catch { /* keep generic text */ } }
      setMsg({ ok: false, text })
    } finally { setBusy(false) }
  }
  const sel = (label: string, v: string, set: (s: string) => void, opts: string[][]) => (
    <label className="text-sm">{label}<select className="mt-1 block rounded border px-2 py-1.5" value={v} onChange={(e) => set(e.target.value)}>{opts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
  )
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-5xl space-y-4">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Reports</h1><Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        <section className="rounded-xl bg-white shadow p-4 flex flex-wrap items-end gap-3">
          {sel('Report', type, setType, TYPES)}
          {sel('Format', format, setFormat, [['pdf', 'PDF'], ['xlsx', 'Excel']])}
          {type !== 'quality' && sel('Parameter', parameter, setParameter, params.map((p) => [p, p]))}
          {['spc', 'control_chart'].includes(type) && sel('Chart', o.chart, (v) => setO({ ...o, chart: v }), ['IMR', 'XBAR_R', 'XBAR_S', 'EWMA', 'CUSUM'].map((c) => [c, c]))}
          {['spc', 'control_chart'].includes(type) && ['XBAR_R', 'XBAR_S'].includes(o.chart) &&
            <label className="text-sm">Subgroup<input className="mt-1 block w-20 rounded border px-2 py-1.5" value={o.subgroup} onChange={(e) => setO({ ...o, subgroup: e.target.value })} /></label>}
          {['spc', 'control_chart', 'capability'].includes(type) && (['usl', 'lsl', 'target'] as const).map((k) => (
            <label key={k} className="text-sm">{k.toUpperCase()}<input className="mt-1 block w-24 rounded border px-2 py-1.5" value={o[k]} onChange={(e) => setO({ ...o, [k]: e.target.value })} /></label>))}
          <button onClick={generate} disabled={busy} className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{busy ? 'Generating…' : 'Generate'}</button>
        </section>
        {msg && <p role={msg.ok ? 'status' : 'alert'} className={`rounded px-3 py-2 text-sm ${msg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{msg.text}</p>}
      </div>
    </main>
  )
}

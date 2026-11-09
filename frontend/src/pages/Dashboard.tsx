import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard, getInsights, type Result } from '../api/analysis'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import FilterBar from '../components/FilterBar'

const BADGE: Record<string, string> = {
  IN_CONTROL: 'bg-green-100 text-green-800', ATTENTION: 'bg-amber-100 text-amber-800', OUT_OF_CONTROL: 'bg-red-100 text-red-800',
  NO_DATA: 'bg-slate-100 text-slate-600', NOT_ENOUGH_DATA: 'bg-slate-100 text-slate-600',
}
const f4 = (v: number | null | undefined) => (v == null ? '—' : Number(v.toPrecision(4)).toString())

export default function Dashboard() {
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [d, setD] = useState<Result | null>(null)
  const [insights, setInsights] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let off = false
    setLoading(true); setError(null)
    Promise.all([data.sensorOptions(filters), getDashboard(filters), getInsights(filters)])
      .then(([o, dash, ins]) => { if (!off) { setOptions(o); setD(dash); setInsights(ins) } })
      .catch((e) => { if (!off) setError(errorMessage(e)) })
      .finally(() => { if (!off) setLoading(false) })
    return () => { off = true }
  }, [filters])

  const k = d?.kpis
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Overview <span className="text-sm font-normal text-slate-500">(historical process status)</span></h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        {error && <p role="alert" className="rounded bg-red-50 text-red-700 px-3 py-2 text-sm">{error}</p>}
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {d && (
          <>
            <section className="rounded-xl bg-white shadow p-4 flex items-center gap-4" data-testid="status">
              <span className={`rounded px-3 py-1 text-sm font-semibold ${BADGE[d.status]}`} data-testid="status-badge">{String(d.status).replace(/_/g, ' ')}</span>
              <span className="text-sm text-slate-600">{d.status_reason}</span>
            </section>
            {d.status !== 'NO_DATA' && k && (
              <dl className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="kpis">
                {[['Readings', k.readings], ['Parameters', k.parameters], ['Datasets', k.datasets], ['Rule violations', k.violations_total],
                  ['Out-of-control points', k.out_of_control_points], ['In control', k.parameters_in_control], ['Need attention', k.parameters_needing_attention],
                  ['Period', k.first ? `${String(k.first).slice(0, 10)} → ${String(k.last).slice(0, 10)}` : '—']].map(([l, v]) => (
                  <div key={l as string} className="rounded-xl bg-white shadow p-3"><dt className="text-xs text-slate-500">{l}</dt><dd className="text-xl font-semibold" data-testid={`kpi-${l}`}>{v as string}</dd></div>
                ))}
              </dl>
            )}
            {d.parameters.length > 0 && (
              <section className="rounded-xl bg-white shadow p-4 overflow-auto">
                <h2 className="font-medium mb-2">Parameters</h2>
                <table className="min-w-full text-sm" data-testid="param-table"><thead><tr className="text-left text-slate-500">
                  <th className="pr-4">Parameter</th><th className="pr-4">Status</th><th className="pr-4">n</th><th className="pr-4">Mean</th><th className="pr-4">Std</th><th className="pr-4">Violations</th><th>Out of control</th></tr></thead>
                  <tbody>{(d.parameters as Result[]).map((c) => (
                    <tr key={c.parameter} className="border-t"><td className="pr-4 font-medium">{c.parameter}</td>
                      <td className="pr-4"><span className={`rounded px-1.5 text-xs ${BADGE[c.status]}`}>{String(c.status).replace(/_/g, ' ')}</span></td>
                      <td className="pr-4">{c.n}</td><td className="pr-4">{f4(c.mean)}</td><td className="pr-4">{f4(c.std)}</td><td className="pr-4">{c.violations}</td><td>{c.out_of_control_points}</td></tr>))}</tbody></table>
              </section>
            )}
            {d.quality && (
              <section className="rounded-xl bg-white shadow p-4" data-testid="quality">
                <h2 className="font-medium mb-2">Quality summary</h2>
                <p className="text-sm">{d.quality.samples} samples · {d.quality.batches} batches · {d.quality.sample_size} inspected · {d.quality.defective_count} defective
                  {d.quality.defect_rate != null && <> · defect rate <strong>{(d.quality.defect_rate * 100).toPrecision(3)}%</strong></>}
                  {d.quality.p_chart_out_of_control != null && <> · {d.quality.p_chart_out_of_control} sample(s) beyond P-chart limits</>}</p>
              </section>
            )}
            {insights && insights.insights.length > 0 && (
              <section className="rounded-xl bg-white shadow p-4" data-testid="insights">
                <h2 className="font-medium mb-2">AI insights <span className="text-xs font-normal text-slate-500">(each derived from the analysis results above)</span></h2>
                <ul className="space-y-1 text-sm">{(insights.insights as Result[]).map((i, n) => (
                  <li key={n}><span className={`mr-2 rounded px-1.5 text-xs ${i.severity === 'HIGH' ? 'bg-red-100 text-red-700' : i.severity === 'MEDIUM' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100'}`}>{i.severity}</span>{i.text}</li>))}</ul>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  )
}

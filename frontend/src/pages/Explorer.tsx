import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import FilterBar from '../components/FilterBar'

export default function Explorer() {
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [summary, setSummary] = useState<data.SensorSummary | null>(null)
  const [rows, setRows] = useState<{ items: data.SensorRow[]; total: number; page: number } | null>(null)
  const [page, setPage] = useState(1)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { setPage(1) }, [filters])

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    Promise.all([data.sensorOptions(filters), data.sensorSummary(filters), data.sensorRows(filters, page, 25)])
      .then(([o, s, r]) => { if (!cancelled) { setOptions(o); setSummary(s); setRows(r) } })
      .catch((e) => { if (!cancelled) setError(errorMessage(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filters, page])

  const pages = rows ? Math.max(1, Math.ceil(rows.total / 25)) : 1

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Historical data explorer</h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        {error && <p role="alert" className="rounded bg-red-50 text-red-700 px-3 py-2 text-sm">{error}</p>}

        <section className="rounded-xl bg-white shadow p-4" data-testid="summary">
          {summary && (
            <div className="space-y-2">
              <p className="text-sm">
                <strong data-testid="total">{summary.total_rows}</strong> readings from {summary.datasets} dataset(s)
                {summary.first && <> · {summary.first.replace('T', ' ').slice(0, 16)} → {summary.last?.replace('T', ' ').slice(0, 16)}</>}
                {loading && <span className="ml-2 text-slate-400">updating…</span>}
              </p>
              <div className="flex flex-wrap gap-3">
                {summary.parameters.map((p) => (
                  <div key={p.parameter} className="rounded bg-slate-50 px-3 py-2 text-xs">
                    <div className="font-semibold">{p.parameter}</div>
                    n={p.n} · min {p.min} · max {p.max} · mean {p.mean.toFixed(3)}
                  </div>
                ))}
              </div>
            </div>
          )}
          {summary && summary.total_rows === 0 && <p className="text-sm text-slate-500">No data matches these filters.</p>}
        </section>

        <section className="rounded-xl bg-white shadow p-4 overflow-auto">
          <table className="min-w-full text-sm" data-testid="rows">
            <thead><tr className="text-left text-slate-500">
              {['Time', 'Parameter', 'Value', 'Plant', 'Line', 'Asset', 'Tag', 'Batch'].map((h) => <th key={h} className="pr-4 py-1">{h}</th>)}
            </tr></thead>
            <tbody>{rows?.items.map((r, i) => (
              <tr key={i} className="border-t">
                <td className="pr-4 py-1 whitespace-nowrap">{r.measured_at.replace('T', ' ').slice(0, 19)}</td>
                <td className="pr-4">{r.parameter}</td><td className="pr-4">{r.value}</td><td className="pr-4">{r.plant_code}</td>
                <td className="pr-4">{r.line_code}</td><td className="pr-4">{r.asset_code}</td><td className="pr-4">{r.tag_name}</td><td>{r.batch_id}</td>
              </tr>
            ))}</tbody>
          </table>
          <div className="flex items-center gap-3 pt-3 text-sm">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded border px-3 py-1 disabled:opacity-40">Previous</button>
            <span>Page {page} of {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded border px-3 py-1 disabled:opacity-40">Next</button>
          </div>
        </section>
      </div>
    </main>
  )
}

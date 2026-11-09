import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import FilterBar from '../components/FilterBar'

const CORE = ['TEMPERATURE', 'PRESSURE', 'SPEED', 'FLOW']
const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#ca8a04', '#be185d']

const label = (s: data.Series) => [s.plant_code, s.line_code, s.asset_code, s.tag_name].filter(Boolean).join(' / ') || 'all'
const ts = (t: string) => new Date(t.endsWith('Z') ? t : t + 'Z').getTime()          // data has no time zone: treat as UTC for display
const fmt = (ms: number) => new Date(ms).toISOString().replace('T', ' ').slice(0, 16)

export default function Monitoring() {
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [series, setSeries] = useState<data.Series[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    Promise.all([data.sensorOptions(filters), data.sensorSeries(filters)])
      .then(([o, s]) => { if (!cancelled) { setOptions(o); setSeries(s.series) } })
      .catch((e) => { if (!cancelled) setError(errorMessage(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [filters])

  const byParam = useMemo(() => {
    const m = new Map<string, data.Series[]>()
    series?.forEach((s) => m.set(s.parameter, [...(m.get(s.parameter) ?? []), s]))
    return [...m.entries()].sort(([a], [b]) => {
      const ia = CORE.indexOf(a), ib = CORE.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b)
    })
  }, [series])

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Process monitoring <span className="text-sm font-normal text-slate-500">(historical)</span></h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        {error && <p role="alert" className="rounded bg-red-50 text-red-700 px-3 py-2 text-sm">{error}</p>}
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {series && series.length === 0 && !loading && (
          <p className="rounded bg-white shadow p-4 text-sm text-slate-500" data-testid="empty">No historical readings match these filters.</p>
        )}
        {byParam.map(([param, list]) => (
          <section key={param} className="rounded-xl bg-white shadow p-4" data-testid={`chart-${param}`}>
            <h2 className="font-medium mb-1">{param}
              <span className="ml-2 text-xs font-normal text-slate-500">
                {list.reduce((a, s) => a + s.n_total, 0)} readings{list.some((s) => s.aggregated) && ' · aggregated into time buckets (average, with min/max shown dashed)'}
              </span>
            </h2>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart margin={{ top: 5, right: 16, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" dataKey="t" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={fmt} fontSize={11} />
                  <YAxis domain={['auto', 'auto']} fontSize={11} width={52} />
                  <Tooltip labelFormatter={(v) => fmt(Number(v))} />
                  <Legend />
                  {list.flatMap((s, i) => {
                    const pts = s.points.map((p) => ({ t: ts(p.t), v: p.v, min: p.min, max: p.max }))
                    const c = COLORS[i % COLORS.length]
                    const lines = [<Line key={`${i}v`} data={pts} dataKey="v" name={label(s)} stroke={c} dot={pts.length < 60} strokeWidth={1.5} isAnimationActive={false} />]
                    if (s.aggregated) {
                      lines.push(<Line key={`${i}a`} data={pts} dataKey="min" name={`${label(s)} min`} stroke={c} strokeOpacity={0.35} strokeDasharray="3 3" dot={false} legendType="none" isAnimationActive={false} />)
                      lines.push(<Line key={`${i}b`} data={pts} dataKey="max" name={`${label(s)} max`} stroke={c} strokeOpacity={0.35} strokeDasharray="3 3" dot={false} legendType="none" isAnimationActive={false} />)
                    }
                    return lines
                  })}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        ))}
      </div>
    </main>
  )
}

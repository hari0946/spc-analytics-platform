import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { runAnalysis, type Chart, type ChartOpts, type Result, type Violation } from '../api/analysis'
import { errorMessage } from '../api/client'
import * as data from '../api/data'
import ControlChartView from '../components/ControlChartView'
import { BoxPlot, Histogram, ProbPlot } from '../components/DistributionView'
import FilterBar from '../components/FilterBar'

const TABS = [
  { id: 'statistics', label: 'Statistical analysis' }, { id: 'control-chart', label: 'Control charts' },
  { id: 'limits', label: 'Control limits' }, { id: 'violations', label: 'Rule violations' },
  { id: 'capability', label: 'Process capability' }, { id: 'distribution', label: 'Distribution' },
] as const
type Tab = (typeof TABS)[number]['id']
const VARIABLE = ['XBAR_R', 'XBAR_S', 'R', 'S', 'IMR', 'EWMA', 'CUSUM']
const ATTRIBUTE = ['P', 'NP', 'C', 'U']
const num = (s: string) => (s.trim() === '' ? undefined : Number(s))
const f4 = (v: number | null | undefined) => (v == null ? '—' : Number(v.toPrecision(5)).toString())

export default function Analysis() {
  const [tab, setTab] = useState<Tab>('statistics')
  const [filters, setFilters] = useState<data.Filters>(data.emptyFilters())
  const [options, setOptions] = useState<data.FilterOptions | null>(null)
  const [parameter, setParameter] = useState('')
  const [chart, setChart] = useState('IMR')
  const [form, setForm] = useState({ subgroup: '5', capSub: '', usl: '', lsl: '', target: '', ruleSet: 'BOTH', lam: '0.2', L: '3', k: '0.5', h: '5' })
  // a result belongs to the tab that produced it; it is never rendered by another tab's component
  const [res, setRes] = useState<{ tab: Tab; data: Result } | null>(null)
  const result = res && res.tab === tab ? res.data : null
  const setResult = (data: Result | null) => setRes(data ? { tab, data } : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { data.sensorOptions(filters).then(setOptions).catch((e) => setError(errorMessage(e))) }, [filters])
  const params = options?.parameters.map((p) => p.value) ?? []
  useEffect(() => { if (!parameter && params.length) setParameter(params[0]) }, [params, parameter])
  useEffect(() => { setResult(null); setError(null) }, [tab])

  const attribute = ATTRIBUTE.includes(chart) && (tab === 'control-chart' || tab === 'limits' || tab === 'violations')
  const run = async () => {
    setBusy(true); setError(null)
    const o: Partial<ChartOpts> = { chart, usl: num(form.usl), lsl: num(form.lsl), target: num(form.target) }
    if (['XBAR_R', 'XBAR_S', 'R', 'S'].includes(chart)) o.subgroup_size = num(form.subgroup)
    if (chart === 'EWMA') { o.lam = num(form.lam); o.L = num(form.L) }
    if (chart === 'CUSUM') { o.k = num(form.k); o.h = num(form.h) }
    if (tab === 'violations') o.rule_set = form.ruleSet
    if (tab === 'capability' && num(form.capSub)) o.subgroup_size = num(form.capSub)
    try { setResult(await runAnalysis(tab, filters, attribute ? undefined : parameter, tab === 'statistics' || tab === 'distribution' ? { usl: o.usl, lsl: o.lsl } : o)) }
    catch (e) { setResult(null); setError(errorMessage(e)) } finally { setBusy(false) }
  }
  const field = (k: keyof typeof form, label: string, w = 'w-24') => (
    <label className="text-sm">{label}<input className={`mt-1 block ${w} rounded border px-2 py-1.5`} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>
  )
  const showChartPick = tab === 'control-chart' || tab === 'limits' || tab === 'violations'
  const showSpecs = tab === 'limits' || tab === 'capability'

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">SPC analysis <span className="text-sm font-normal text-slate-500">(historical data)</span></h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <nav className="flex flex-wrap gap-2" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              className={`rounded px-3 py-1.5 text-sm ${tab === t.id ? 'bg-blue-600 text-white' : 'bg-white shadow'}`}>{t.label}</button>
          ))}
        </nav>
        <FilterBar filters={filters} options={options} onChange={setFilters} datasetLabel={(id) => `${id.slice(0, 8)}…`} />
        <div className="rounded-xl bg-white shadow p-4 flex flex-wrap items-end gap-3">
          {!attribute && (
            <label className="text-sm">Parameter
              <select className="mt-1 block rounded border px-2 py-1.5" value={parameter} onChange={(e) => setParameter(e.target.value)}>
                {params.map((p) => <option key={p}>{p}</option>)}
              </select>
            </label>
          )}
          {showChartPick && (
            <label className="text-sm">Chart
              <select className="mt-1 block rounded border px-2 py-1.5" value={chart} onChange={(e) => setChart(e.target.value)}>
                {[...VARIABLE, ...ATTRIBUTE].map((c) => <option key={c} value={c}>{c.replace('_', '-')}</option>)}
              </select>
            </label>
          )}
          {showChartPick && ['XBAR_R', 'XBAR_S', 'R', 'S'].includes(chart) && field('subgroup', 'Subgroup size')}
          {tab === 'capability' && field('capSub', 'Subgroup size (opt.)')}
          {showChartPick && chart === 'EWMA' && <>{field('lam', 'λ')}{field('L', 'L')}</>}
          {showChartPick && chart === 'CUSUM' && <>{field('k', 'k')}{field('h', 'h')}</>}
          {(showSpecs || tab === 'control-chart') && <>{field('usl', 'USL')}{field('lsl', 'LSL')}{field('target', 'Target')}</>}
          {tab === 'violations' && (
            <label className="text-sm">Rules
              <select className="mt-1 block rounded border px-2 py-1.5" value={form.ruleSet} onChange={(e) => setForm({ ...form, ruleSet: e.target.value })}>
                <option value="BOTH">Western Electric + Nelson</option><option value="WESTERN_ELECTRIC">Western Electric</option><option value="NELSON">Nelson</option>
              </select>
            </label>
          )}
          <button onClick={run} disabled={busy || (!attribute && !parameter)} className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">
            {busy ? 'Running…' : 'Run analysis'}
          </button>
        </div>
        {error && <p role="alert" className="rounded bg-red-50 text-red-700 px-3 py-2 text-sm">{error}</p>}
        {result && <section className="rounded-xl bg-white shadow p-4 space-y-4" data-testid="result">
          {(result.notes as string[] | undefined)?.map((n) => <p key={n} className="text-sm text-amber-700">⚠ {n}</p>)}
          {tab === 'statistics' && <Stats r={result} />}
          {(tab === 'control-chart' || tab === 'limits') && <Charts r={result} />}
          {tab === 'limits' && <Limits r={result} />}
          {tab === 'violations' && <Violations r={result} />}
          {tab === 'capability' && <Capability r={result} />}
          {tab === 'distribution' && <Dist r={result} />}
          <p className="text-xs text-slate-400">Analysis {result.analysis_id} is stored in Snowflake.</p>
        </section>}
      </div>
    </main>
  )
}

const Card = ({ k, v }: { k: string; v: string | number | null | undefined }) => (
  <div className="rounded bg-slate-50 p-3"><dt className="text-xs text-slate-500">{k}</dt><dd className="text-lg font-semibold" data-testid={`card-${k}`}>{typeof v === 'number' ? f4(v) : v ?? '—'}</dd></div>
)

function Stats({ r }: { r: Result }) {
  const s = r.statistics
  const keys: [string, string][] = [['n', 'Count'], ['mean', 'Mean'], ['median', 'Median'], ['mode', 'Mode'], ['min', 'Minimum'], ['max', 'Maximum'], ['range', 'Range'],
    ['variance', 'Variance'], ['std', 'Std deviation'], ['skewness', 'Skewness'], ['kurtosis', 'Kurtosis'], ['iqr', 'IQR']]
  return (
    <>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">{keys.map(([k, l]) => <Card key={k} k={l} v={s[k]} />)}</dl>
      <div className="grid sm:grid-cols-2 gap-4 text-sm">
        <table className="w-full"><caption className="text-left font-medium">Quartiles</caption><tbody>
          {['q1', 'q2', 'q3'].map((q) => <tr key={q} className="border-t"><td>{q.toUpperCase()}</td><td className="text-right">{f4(s[q])}</td></tr>)}</tbody></table>
        <table className="w-full"><caption className="text-left font-medium">Percentiles</caption><tbody>
          {Object.entries(s.percentiles as Record<string, number>).map(([k, v]) => <tr key={k} className="border-t"><td>{k.toUpperCase()}</td><td className="text-right">{f4(v)}</td></tr>)}</tbody></table>
      </div>
    </>
  )
}

function Charts({ r }: { r: Result }) {
  const charts = r.charts as Record<string, Chart>
  return <>{Object.entries(charts).map(([n, c]) => <ControlChartView key={n} name={n} chart={c} spec={n === r.primary ? r.spec : undefined} />)}</>
}

function Limits({ r }: { r: Result }) {
  const a = r.assessment as Record<string, number | boolean | null>
  return (
    <div className="grid sm:grid-cols-2 gap-4 text-sm">
      <table className="w-full" data-testid="limits"><caption className="text-left font-medium">Limits</caption>
        <thead><tr className="text-left text-slate-500"><th>Chart</th><th>UCL</th><th>CL</th><th>LCL</th></tr></thead><tbody>
          {Object.entries(r.limits as Record<string, { ucl: number; cl: number; lcl: number }>).map(([n, l]) => (
            <tr key={n} className="border-t"><td>{n}</td><td>{f4(l.ucl)}</td><td>{f4(l.cl)}</td><td>{f4(l.lcl)}</td></tr>))}
          <tr className="border-t"><td>USL / LSL / Target</td><td>{f4(r.spec.usl)}</td><td>{f4(r.spec.lsl)}</td><td>{f4(r.spec.target)}</td></tr></tbody></table>
      <table className="w-full"><caption className="text-left font-medium">Assessment</caption><tbody>
        {Object.entries(a).map(([k, v]) => <tr key={k} className="border-t"><td>{k.replace(/_/g, ' ')}</td><td className="text-right">{typeof v === 'boolean' ? (v ? 'yes' : 'no') : f4(v as number)}</td></tr>)}</tbody></table>
    </div>
  )
}

function Violations({ r }: { r: Result }) {
  const v = r.violations as Violation[]
  return (
    <>
      <p className="text-sm">{r.summary.total} violation(s) on {r.summary.points_flagged} point(s). {Object.entries(r.summary.by_type as Record<string, number>).map(([k, n]) => `${k} ${n}`).join(' · ')}</p>
      <div className="overflow-auto max-h-96"><table className="min-w-full text-sm" data-testid="violations">
        <thead><tr className="text-left text-slate-500"><th className="pr-4">Type</th><th className="pr-4">Timestamp</th><th className="pr-4">Parameter</th><th className="pr-4">Observed</th><th className="pr-4">Rule</th><th>Severity</th></tr></thead>
        <tbody>{v.map((x, i) => (
          <tr key={i} className="border-t"><td className="pr-4">{x.violation_type}</td><td className="pr-4 whitespace-nowrap">{x.timestamp}</td><td className="pr-4">{x.parameter}</td>
            <td className="pr-4">{f4(x.observed_value)}</td><td className="pr-4">{x.rule_id}: {x.name}</td>
            <td><span className={`rounded px-1.5 text-xs ${x.severity === 'HIGH' ? 'bg-red-100 text-red-700' : x.severity === 'MEDIUM' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100'}`}>{x.severity}</span></td></tr>))}</tbody></table>
        {v.length === 0 && <p className="text-sm text-slate-500 py-2">No violations.</p>}</div>
    </>
  )
}

function Capability({ r }: { r: Result }) {
  const c = r.capability
  return (
    <>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {(['cp', 'cpk', 'pp', 'ppk', 'cpu', 'cpl'] as const).map((k) => <Card key={k} k={k.toUpperCase()} v={c[k]} />)}
        <Card k="PPM (observed)" v={c.ppm_observed_total} /><Card k="Yield % (observed)" v={c.yield_observed} />
      </dl>
      {(c.warnings as string[]).map((w) => <p key={w} className="text-sm text-amber-700">⚠ {w}</p>)}
      <p className="text-xs text-slate-500">n={c.n} · USL {f4(c.usl)} · LSL {f4(c.lsl)} · σ within {f4(c.sigma_within)} ({c.sigma_within_method}) · σ overall {f4(c.sigma_overall)} · expected PPM {f4(c.ppm_expected_overall)}</p>
    </>
  )
}

function Dist({ r }: { r: Result }) {
  const d = r.distribution
  return (
    <>
      <p className="text-sm" data-testid="normality">{d.normality.conclusion}</p>
      <Histogram h={d.histogram} />
      <BoxPlot b={d.boxplot} />
      <p className="text-xs text-slate-500">Q1 {f4(d.boxplot.q1)} · median {f4(d.boxplot.median)} · Q3 {f4(d.boxplot.q3)} · {d.boxplot.n_outliers} outlier(s)</p>
      <ProbPlot p={d.probability_plot} />
      <table className="w-full text-sm"><caption className="text-left font-medium">Normality tests</caption><thead><tr className="text-left text-slate-500"><th>Test</th><th>Statistic</th><th>p-value</th><th>Normal?</th></tr></thead><tbody>
        {(d.normality.tests as Result[]).map((t) => <tr key={t.test} className="border-t"><td>{t.test}</td><td>{f4(t.statistic)}</td><td>{f4(t.p_value)}</td><td>{t.normal == null ? '—' : t.normal ? 'yes' : 'no'}</td></tr>)}</tbody></table>
      {d.fit && <table className="w-full text-sm"><caption className="text-left font-medium">Distribution fitting (ranked by AIC)</caption><thead><tr className="text-left text-slate-500"><th>Rank</th><th>Distribution</th><th>AIC</th><th>KS p</th></tr></thead><tbody>
        {(d.fit.fits as Result[]).map((x) => <tr key={x.distribution} className="border-t"><td>{x.rank}</td><td>{x.distribution}</td><td>{f4(x.aic)}</td><td>{f4(x.ks_p_value)}</td></tr>)}</tbody></table>}
    </>
  )
}

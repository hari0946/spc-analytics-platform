import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import type { Result } from '../api/analysis'

export function Histogram({ h, spec }: { h: Result; spec?: { usl?: number | null; lsl?: number | null; target?: number | null } }) {
  const data = h.bin_centers.map((c: number, i: number) => ({ x: Number(c.toPrecision(5)), count: h.counts[i], normal: h.normal_expected_counts?.[i] }))
  return (
    <div className="h-64" data-testid="histogram">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 5, right: 16, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="x" fontSize={11} /><YAxis fontSize={11} width={40} /><Tooltip />
          <Bar dataKey="count" name="Readings" fill="#93c5fd" stroke="#2563eb" />
          {h.normal_expected_counts && <Line dataKey="normal" name="Normal fit" stroke="#dc2626" dot={false} isAnimationActive={false} />}
          {spec?.usl != null && <ReferenceLine x={nearest(data, spec.usl)} stroke="#ea580c" label="USL" />}
          {spec?.lsl != null && <ReferenceLine x={nearest(data, spec.lsl)} stroke="#ea580c" label="LSL" />}
          {spec?.target != null && <ReferenceLine x={nearest(data, spec.target)} stroke="#0891b2" label="Target" />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
const nearest = (d: { x: number }[], v: number) => d.reduce((a, b) => (Math.abs(b.x - v) < Math.abs(a.x - v) ? b : a), d[0]).x

export function BoxPlot({ b }: { b: Result }) {
  const lo = Math.min(b.whisker_low, ...(b.outliers as number[])), hi = Math.max(b.whisker_high, ...(b.outliers as number[]))
  const pad = (hi - lo || 1) * 0.08
  const x = (v: number) => 40 + ((v - (lo - pad)) / (hi - lo + 2 * pad)) * 520
  return (
    <svg viewBox="0 0 600 90" className="w-full max-w-2xl" data-testid="boxplot" role="img" aria-label="Box plot">
      <line x1={x(b.whisker_low)} x2={x(b.whisker_high)} y1={45} y2={45} stroke="#334155" />
      <rect x={x(b.q1)} y={25} width={Math.max(1, x(b.q3) - x(b.q1))} height={40} fill="#bfdbfe" stroke="#2563eb" />
      <line x1={x(b.median)} x2={x(b.median)} y1={25} y2={65} stroke="#1d4ed8" strokeWidth={2} />
      <circle cx={x(b.mean)} cy={45} r={3} fill="#16a34a"><title>mean</title></circle>
      {[b.whisker_low, b.whisker_high].map((w: number) => <line key={w} x1={x(w)} x2={x(w)} y1={35} y2={55} stroke="#334155" />)}
      {(b.outliers as number[]).map((o, i) => <circle key={i} cx={x(o)} cy={45} r={3.5} fill="none" stroke="#dc2626"><title>{o}</title></circle>)}
      <text x={x(b.median)} y={82} fontSize={10} textAnchor="middle">median {Number(b.median.toPrecision(4))}</text>
    </svg>
  )
}

export function ProbPlot({ p }: { p: Result }) {
  const pts = p.theoretical_quantiles.map((q: number, i: number) => ({ q, v: p.ordered_values[i] }))
  const q0 = pts[0].q, q1 = pts[pts.length - 1].q
  const line = [{ q: q0, fit: p.intercept + p.slope * q0 }, { q: q1, fit: p.intercept + p.slope * q1 }]
  return (
    <div className="h-64" data-testid="probplot">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 5, right: 16, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" dataKey="q" name="Theoretical quantile" fontSize={11} domain={['auto', 'auto']} />
          <YAxis type="number" dataKey="v" name="Value" fontSize={11} domain={['auto', 'auto']} width={50} />
          <Tooltip />
          <Scatter data={pts} fill="#2563eb" />
          <Scatter data={line.map((l) => ({ q: l.q, v: l.fit }))} line={{ stroke: '#dc2626' }} shape={() => <g />} />
        </ScatterChart>
      </ResponsiveContainer>
      <p className="text-xs text-slate-500">R² = {p.r_squared.toFixed(4)}</p>
    </div>
  )
}


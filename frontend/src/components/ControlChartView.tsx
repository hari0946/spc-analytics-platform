import { CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Chart } from '../api/analysis'

interface Props { name: string; chart: Chart; spec?: { usl?: number | null; lsl?: number | null; target?: number | null } }

export default function ControlChartView({ name, chart, spec }: Props) {
  const ooc = new Set(chart.out_of_control)
  const data = chart.points.map((v, i) => ({
    i, label: chart.labels[i] ?? String(i), v, ucl: chart.ucl[i], cl: chart.cl[i], lcl: chart.lcl[i], lower: chart.lower_points?.[i],
  }))
  return (
    <div data-testid={`cc-${name}`}>
      <h3 className="text-sm font-medium mb-1">{name} chart
        <span className="ml-2 text-xs font-normal text-slate-500">{chart.n_points} points · {ooc.size} out of control</span></h3>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 5, right: 16, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="i" fontSize={11} />
            <YAxis domain={['auto', 'auto']} fontSize={11} width={56} />
            <Tooltip labelFormatter={(i) => data[Number(i)]?.label ?? String(i)} />
            <Legend />
            <Line dataKey="ucl" name="UCL" stroke="#dc2626" strokeDasharray="5 3" dot={false} isAnimationActive={false} />
            <Line dataKey="cl" name="CL" stroke="#16a34a" dot={false} isAnimationActive={false} />
            <Line dataKey="lcl" name="LCL" stroke="#dc2626" strokeDasharray="5 3" dot={false} isAnimationActive={false} />
            <Line dataKey="v" name="Value" stroke="#2563eb" strokeWidth={1.5} isAnimationActive={false}
              dot={(p: { cx?: number; cy?: number; payload?: { i: number } }) => {
                const i = p.payload?.i ?? -1
                const bad = ooc.has(i)
                if (p.cx == null || p.cy == null) return <g key={i} />
                return <circle key={i} cx={p.cx} cy={p.cy} r={bad ? 4.5 : 2} fill={bad ? '#dc2626' : '#2563eb'} data-ooc={bad ? 'true' : undefined} />
              }} />
            {chart.lower_points && <Line dataKey="lower" name="Lower CUSUM" stroke="#9333ea" dot={false} isAnimationActive={false} />}
            {spec?.usl != null && <ReferenceLine y={spec.usl} stroke="#ea580c" strokeDasharray="2 2" label={{ value: 'USL', fontSize: 10, fill: '#ea580c' }} />}
            {spec?.lsl != null && <ReferenceLine y={spec.lsl} stroke="#ea580c" strokeDasharray="2 2" label={{ value: 'LSL', fontSize: 10, fill: '#ea580c' }} />}
            {spec?.target != null && <ReferenceLine y={spec.target} stroke="#0891b2" strokeDasharray="2 2" label={{ value: 'Target', fontSize: 10, fill: '#0891b2' }} />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

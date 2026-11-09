import { api } from './client'
import { toParams, type Filters } from './data'

export interface ChartOpts {
  chart: string; subgroup_size?: number; subgroup_by?: 'size' | 'batch'; lam?: number; L?: number; k?: number; h?: number
  target?: number; sigma?: number; usl?: number; lsl?: number; rule_set?: string; bins?: number
}
export interface Chart {
  chart: string; n_points: number; points: number[]; lower_points?: number[]; cl: number[]; ucl: number[]; lcl: number[]
  out_of_control: number[]; labels: string[]; params: Record<string, number | string | boolean>
}
export interface Violation {
  rule_set: string; rule_id: string; name: string; violation_type: string; severity: string; point_index: number
  observed_value: number; timestamp: string | null; parameter: string
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Result = Record<string, any>

export async function runAnalysis(kind: string, f: Filters, parameter: string | undefined, o: Partial<ChartOpts> = {}): Promise<Result> {
  const extra: Record<string, string | number> = {}
  if (parameter) extra.parameter = parameter
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '' && !Number.isNaN(v)) extra[k] = v as string | number
  return (await api.get<Result>(`/analysis/${kind}`, { params: toParams(f, extra) })).data
}
export const getDashboard = async (f: Filters): Promise<Result> => (await api.get<Result>('/dashboard', { params: toParams(f) })).data
export const askAssistant = async (question: string, f: Filters): Promise<Result> =>
  (await api.post<Result>('/assistant/ask', {
    question, upload_id: f.upload_id, plant: f.plant, line: f.line, asset: f.asset, tag: f.tag, batch: f.batch,
    start: f.start || null, end: f.end || null,
  })).data
export const getInsights = async (f: Filters): Promise<Result> => (await api.get<Result>('/assistant/summary', { params: toParams(f) })).data

export async function downloadReport(body: Record<string, unknown>, filename: string) {
  const r = await api.post('/reports', body, { responseType: 'blob' })
  const url = URL.createObjectURL(r.data as Blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

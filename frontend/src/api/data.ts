import { api, type Page } from './client'

export interface Filters {
  upload_id: string[]; plant: string[]; line: string[]; asset: string[]; tag: string[]
  parameter: string[]; batch: string[]; start: string; end: string
}
export const emptyFilters = (): Filters => ({
  upload_id: [], plant: [], line: [], asset: [], tag: [], parameter: [], batch: [], start: '', end: '',
})

export interface OptionValue { value: string; count: number }
export interface FilterOptions {
  plants: OptionValue[]; lines: OptionValue[]; assets: OptionValue[]; tags: OptionValue[]
  parameters: OptionValue[]; batches: OptionValue[]; datasets: OptionValue[]
  first: string | null; last: string | null
}
export interface SensorRow {
  upload_id: string; plant_code: string | null; line_code: string | null; asset_code: string | null
  tag_name: string | null; parameter: string; measured_at: string; value: number; batch_id: string | null
}
export interface ParameterSummary { parameter: string; n: number; min: number; max: number; mean: number }
export interface SensorSummary { total_rows: number; first: string | null; last: string | null; datasets: number; parameters: ParameterSummary[] }

/** Repeats list parameters (?plant=P1&plant=P2) and drops empty values. */
export function toParams(f: Filters, extra: Record<string, string | number> = {}): URLSearchParams {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(f)) {
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x))
    else if (v) p.append(k, v)
  }
  for (const [k, v] of Object.entries(extra)) p.append(k, String(v))
  return p
}

export const sensorOptions = async (f: Filters) => (await api.get<FilterOptions>('/data/sensor/options', { params: toParams(f) })).data
export const sensorSummary = async (f: Filters) => (await api.get<SensorSummary>('/data/sensor/summary', { params: toParams(f) })).data
export const sensorRows = async (f: Filters, page = 1, pageSize = 50) =>
  (await api.get<Page<SensorRow>>('/data/sensor', { params: toParams(f, { page, page_size: pageSize }) })).data

export interface SeriesPoint { t: string; v: number; min: number; max: number; n: number }
export interface Series {
  plant_code: string | null; line_code: string | null; asset_code: string | null; tag_name: string | null
  parameter: string; n_total: number; aggregated: boolean; bucket_seconds: number | null; points: SeriesPoint[]
}
export const sensorSeries = async (f: Filters, maxPoints = 1500) =>
  (await api.get<{ max_points: number; series: Series[] }>('/data/sensor/series', { params: toParams(f, { max_points: maxPoints }) })).data

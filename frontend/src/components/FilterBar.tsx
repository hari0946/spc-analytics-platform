import type { FilterOptions, Filters, OptionValue } from '../api/data'

type ListKey = 'upload_id' | 'plant' | 'line' | 'asset' | 'tag' | 'parameter' | 'batch'

const FIELDS: { key: ListKey; label: string; opt: keyof FilterOptions }[] = [
  { key: 'upload_id', label: 'Dataset', opt: 'datasets' },
  { key: 'plant', label: 'Plant', opt: 'plants' },
  { key: 'line', label: 'Production line', opt: 'lines' },
  { key: 'asset', label: 'Machine / asset', opt: 'assets' },
  { key: 'tag', label: 'Sensor / tag', opt: 'tags' },
  { key: 'parameter', label: 'Parameter', opt: 'parameters' },
  { key: 'batch', label: 'Batch', opt: 'batches' },
]

interface Props {
  filters: Filters
  options: FilterOptions | null
  onChange: (f: Filters) => void
  datasetLabel?: (id: string) => string
}

/** Reusable historical-data filter bar. Option lists are faceted by the server (narrowed by the other filters). */
export default function FilterBar({ filters, options, onChange, datasetLabel }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch })
  const toggle = (key: ListKey, value: string) => {
    const cur = filters[key]
    set({ [key]: cur.includes(value) ? cur.filter((x) => x !== value) : [...cur, value] } as Partial<Filters>)
  }
  const choices = (f: (typeof FIELDS)[number]): OptionValue[] => {
    const fromServer = (options?.[f.opt] as OptionValue[] | undefined) ?? []
    // keep selected values visible even if the server narrowed them away
    const missing = filters[f.key].filter((v) => !fromServer.some((o) => o.value === v)).map((v) => ({ value: v, count: 0 }))
    return [...fromServer, ...missing]
  }

  return (
    <div className="rounded-xl bg-white shadow p-4 space-y-3" data-testid="filter-bar">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {FIELDS.map((f) => {
          const list = choices(f)
          if (list.length === 0 && f.key !== 'upload_id') return null
          return (
            <fieldset key={f.key} className="text-sm">
              <legend className="font-medium mb-1">{f.label}</legend>
              <div className="max-h-28 overflow-auto space-y-0.5 border rounded p-1.5">
                {list.map((o) => (
                  <label key={o.value} className="flex items-center gap-1.5">
                    <input type="checkbox" checked={filters[f.key].includes(o.value)} onChange={() => toggle(f.key, o.value)} />
                    <span className="truncate">{f.key === 'upload_id' && datasetLabel ? datasetLabel(o.value) : o.value}</span>
                    <span className="ml-auto text-xs text-slate-400">{o.count}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )
        })}
        <label className="text-sm">Start date
          <input type="date" className="mt-1 block w-full rounded border px-2 py-1.5" value={filters.start}
            min={options?.first?.slice(0, 10)} max={filters.end || options?.last?.slice(0, 10)}
            onChange={(e) => set({ start: e.target.value })} />
        </label>
        <label className="text-sm">End date
          <input type="date" className="mt-1 block w-full rounded border px-2 py-1.5" value={filters.end}
            min={filters.start || options?.first?.slice(0, 10)} max={options?.last?.slice(0, 10)}
            onChange={(e) => set({ end: e.target.value })} />
        </label>
      </div>
      <button className="text-sm text-slate-600 hover:underline"
        onClick={() => onChange({ upload_id: [], plant: [], line: [], asset: [], tag: [], parameter: [], batch: [], start: '', end: '' })}>
        Clear filters
      </button>
    </div>
  )
}

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import * as api from '../api/client'
import { useAuth } from '../auth/AuthContext'

const SENSOR_FIELDS = ['timestamp', 'parameter', 'value', 'tag', 'asset', 'line', 'plant', 'batch']
const PROD_FIELDS = ['timestamp', 'batch', 'product', 'asset', 'line', 'plant', 'units_produced', 'sample_size', 'defective_count', 'defect_count']
const REQUIRED = new Set(['timestamp'])

type Mapping = Record<string, string | string[]>

export default function Uploads() {
  const { user } = useAuth()
  const canUpload = !!user && (user.permissions.includes('*') || user.permissions.includes('data:upload'))
  const [file, setFile] = useState<File | null>(null)
  const [type, setType] = useState<api.DatasetType>('SENSOR')
  const [allowDup, setAllowDup] = useState(false)
  const [current, setCurrent] = useState<api.Upload | null>(null)
  const [mapping, setMapping] = useState<Mapping>({})
  const [report, setReport] = useState<api.Report | null>(null)
  const [tsFormat, setTsFormat] = useState('')
  const [history, setHistory] = useState<api.Page<api.Upload> | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const refresh = useCallback(() => api.listUploads().then(setHistory).catch((e) => setMsg({ kind: 'err', text: api.errorMessage(e) })), [])
  useEffect(() => { void refresh() }, [refresh])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setMsg(null)
    try { await fn() } catch (e) { setMsg({ kind: 'err', text: api.errorMessage(e) }) } finally { setBusy(false) }
  }

  const onUpload = (e: FormEvent) => {
    e.preventDefault()
    if (!file) return
    void run(async () => {
      const up = await api.createUpload(file, type, allowDup)
      setCurrent(up)
      setMapping((up.preview?.suggested_mapping ?? {}) as Mapping)
      setMsg({ kind: 'ok', text: `Staged ${up.rows_received} rows. Review the column mapping below.` })
      await refresh()
    })
  }

  const onSaveAndLoad = () => current && void run(async () => {
    const clean: Mapping = Object.fromEntries(Object.entries(mapping).filter(([, v]) => (Array.isArray(v) ? v.length : v)))
    await api.saveMapping(current.upload_id, clean)
    const loaded = await api.loadUpload(current.upload_id)
    setCurrent(loaded)
    setMsg({ kind: 'ok', text: `Loaded ${loaded.rows_loaded} rows into BRONZE.` })
    await refresh()
  })

  const onProcess = () => current && void run(async () => {
    setReport(null)
    let up = await api.processUpload(current.upload_id, tsFormat.trim() || undefined)
    setCurrent((c) => (c ? { ...c, status: up.status } : c))
    for (let i = 0; i < 150 && up.status === 'PROCESSING'; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      up = await api.getUpload(current.upload_id)
    }
    setCurrent((c) => (c ? { ...c, status: up.status, error_message: up.error_message } : c))
    if (up.status === 'COMPLETED') {
      setReport(await api.getReport(current.upload_id))
      setMsg({ kind: 'ok', text: 'Processing finished. See the validation report below.' })
    } else {
      setMsg({ kind: 'err', text: up.error_message ?? `Processing ended with status ${up.status}` })
    }
    await refresh()
  })

  const onView = (id: string) => void run(async () => {
    const up = await api.getUpload(id)
    setCurrent(up)
    setMapping((up.column_mapping ?? up.preview?.suggested_mapping ?? {}) as Mapping)
    setReport(up.status === 'COMPLETED' ? await api.getReport(id) : null)
  })

  const onDelete = (id: string) => void run(async () => {
    await api.deleteUpload(id)
    if (current?.upload_id === id) { setCurrent(null); setReport(null) }
    await refresh()
  })

  const cols = current?.preview?.columns ?? []
  const fields = current?.dataset_type === 'PRODUCTION' ? PROD_FIELDS : SENSOR_FIELDS
  const loaded = ['LOADED_BRONZE', 'PROCESSING', 'COMPLETED', 'PROCESS_FAILED'].includes(current?.status ?? '')
  const wide = Array.isArray(mapping.value_columns)

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Historical data upload</h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>

        {msg && <p role={msg.kind === 'err' ? 'alert' : 'status'}
          className={`rounded px-3 py-2 text-sm ${msg.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>{msg.text}</p>}

        {canUpload ? (
          <form onSubmit={onUpload} className="rounded-xl bg-white shadow p-5 flex flex-wrap items-end gap-4">
            <label className="text-sm">File (.csv, .xlsx, .xls)
              <input type="file" accept=".csv,.xlsx,.xls" className="mt-1 block text-sm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <label className="text-sm">Dataset type
              <select className="mt-1 block rounded border px-2 py-1.5" value={type} onChange={(e) => setType(e.target.value as api.DatasetType)}>
                <option value="SENSOR">Sensor readings</option>
                <option value="PRODUCTION">Production / quality</option>
              </select>
            </label>
            <label className="text-sm flex items-center gap-2">
              <input type="checkbox" checked={allowDup} onChange={(e) => setAllowDup(e.target.checked)} /> Allow re-upload of identical file
            </label>
            <button disabled={!file || busy} className="rounded bg-blue-600 px-4 py-2 text-white text-sm disabled:opacity-50">
              {busy ? 'Working…' : 'Upload & preview'}
            </button>
          </form>
        ) : <p className="text-sm text-slate-500">Your role can view upload history but not upload files.</p>}

        {current?.preview && (
          <section className="rounded-xl bg-white shadow p-5 space-y-4">
            <h2 className="font-medium">{current.file_name} <span className="text-sm text-slate-500">— {current.status}</span></h2>
            <p className="text-sm text-slate-600">
              {current.rows_received} rows · {current.empty_rows_skipped} empty skipped · {current.duplicate_rows} duplicates
            </p>
            {current.preview.warnings.map((w) => <p key={w} className="text-sm text-amber-700">⚠ {w}</p>)}

            <div className="overflow-auto max-h-64 border rounded">
              <table className="min-w-full text-xs">
                <thead className="bg-slate-100 sticky top-0"><tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left">{c}</th>)}</tr></thead>
                <tbody>{current.preview.rows.map((r, i) => (
                  <tr key={i} className="odd:bg-white even:bg-slate-50">{r.map((v, j) => <td key={j} className="px-2 py-1 whitespace-nowrap">{v ?? <em className="text-slate-400">null</em>}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>

            {!loaded && (
              <>
                <h3 className="font-medium text-sm">Column mapping</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {fields.filter((f) => !(wide && (f === 'parameter' || f === 'value'))).map((f) => (
                    <label key={f} className="text-sm">{f}{REQUIRED.has(f) && ' *'}
                      <select className="mt-1 block w-full rounded border px-2 py-1.5" value={(mapping[f] as string) ?? ''}
                        onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}>
                        <option value="">— not mapped —</option>
                        {cols.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
                {current.dataset_type === 'SENSOR' && (
                  <fieldset className="text-sm">
                    <legend className="font-medium">Wide layout: one column per measured parameter</legend>
                    <div className="flex flex-wrap gap-3 mt-1">
                      {cols.map((c) => {
                        const sel = (mapping.value_columns as string[] | undefined) ?? []
                        return (
                          <label key={c} className="flex items-center gap-1">
                            <input type="checkbox" checked={sel.includes(c)} onChange={(e) => {
                              const next = e.target.checked ? [...sel, c] : sel.filter((x) => x !== c)
                              const m: Mapping = { ...mapping }
                              if (next.length) { m.value_columns = next; delete m.parameter; delete m.value }
                              else {                                   // back to the long layout: restore the suggested columns
                                delete m.value_columns
                                const sug = current?.preview?.suggested_mapping ?? {}
                                if (sug.parameter) m.parameter = sug.parameter
                                if (sug.value) m.value = sug.value
                              }
                              setMapping(m)
                            }} /> {c}
                          </label>
                        )
                      })}
                    </div>
                  </fieldset>
                )}
                <button onClick={onSaveAndLoad} disabled={busy || !mapping.timestamp}
                  className="rounded bg-green-600 px-4 py-2 text-white text-sm disabled:opacity-50">
                  {busy ? 'Loading…' : 'Save mapping & load into BRONZE'}
                </button>
              </>
            )}
          </section>
        )}

        {current && loaded && canUpload && (
          <section className="rounded-xl bg-white shadow p-5 space-y-3">
            <h2 className="font-medium">Process data <span className="text-sm text-slate-500">— {current.status}</span></h2>
            <p className="text-sm text-slate-600">Cleans, validates and de-duplicates the raw data (SILVER), then builds analysis-ready tables (GOLD). Nothing is imputed.</p>
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-sm">Timestamp format (optional)
                <input className="mt-1 block rounded border px-2 py-1.5" placeholder="e.g. DD/MM/YYYY HH24:MI" value={tsFormat}
                  onChange={(e) => setTsFormat(e.target.value)} />
              </label>
              <button onClick={onProcess} disabled={busy || current.status === 'PROCESSING'}
                className="rounded bg-indigo-600 px-4 py-2 text-white text-sm disabled:opacity-50">
                {current.status === 'PROCESSING' ? 'Processing…' : current.status === 'COMPLETED' ? 'Re-process' : 'Process data'}
              </button>
            </div>
            {current.status === 'PROCESS_FAILED' && <p role="alert" className="text-sm text-red-600">{current.error_message}</p>}
          </section>
        )}

        {report && (
          <section className="rounded-xl bg-white shadow p-5 space-y-3" data-testid="report">
            <h2 className="font-medium">Validation report
              <span className={`ml-2 rounded px-2 py-0.5 text-xs ${report.all_checks_passed ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                {report.all_checks_passed ? 'all checks passed' : 'checks failed'}
              </span></h2>
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              {([['Raw rows (BRONZE)', report.counts.bronze_rows], ['Accepted (SILVER)', report.counts.silver_rows],
                 ['Analysis-ready (GOLD)', report.counts.gold_rows], ['Rejected', report.counts.rejected_rows]] as const).map(([k, v]) => (
                <div key={k} className="rounded bg-slate-50 p-3"><dt className="text-slate-500">{k}</dt><dd className="text-xl font-semibold">{v}</dd></div>
              ))}
            </dl>
            {Object.keys(report.counts.rejected_by_reason).length > 0 && (
              <p className="text-sm">Rejected by reason: {Object.entries(report.counts.rejected_by_reason).map(([r, n]) => `${r} ${n}`).join(' · ')}</p>
            )}
            <ul className="text-sm space-y-1">
              {report.checks.map((c) => (
                <li key={c.name}>{c.passed ? '✔' : '✘'} <strong>{c.name}</strong> <span className="text-slate-500">— {c.detail}</span></li>
              ))}
            </ul>
            <p className="text-xs text-slate-500">{report.policy}</p>
          </section>
        )}

        <section className="rounded-xl bg-white shadow p-5">
          <h2 className="font-medium mb-3">Upload history {history && <span className="text-sm text-slate-500">({history.total})</span>}</h2>
          <div className="overflow-auto">
            <table className="min-w-full text-sm" data-testid="history">
              <thead><tr className="text-left text-slate-500">
                <th className="py-1 pr-3">File</th><th className="pr-3">Type</th><th className="pr-3">Status</th>
                <th className="pr-3">Rows</th><th className="pr-3">Uploaded</th><th></th></tr></thead>
              <tbody>{history?.items.map((u) => (
                <tr key={u.upload_id} className="border-t">
                  <td className="py-1 pr-3">{u.file_name}</td><td className="pr-3">{u.dataset_type}</td>
                  <td className="pr-3" title={u.error_message ?? ''}>{u.status}</td>
                  <td className="pr-3">{u.rows_loaded ?? u.rows_received ?? '—'}</td>
                  <td className="pr-3">{new Date(u.uploaded_at + 'Z').toLocaleString()}</td>
                  <td className="whitespace-nowrap">
                    <button onClick={() => onView(u.upload_id)} className="text-blue-600 hover:underline mr-3">View</button>
                    {canUpload && <button onClick={() => onDelete(u.upload_id)} className="text-red-600 hover:underline">Delete</button>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
            {history && history.items.length === 0 && <p className="text-sm text-slate-500 py-2">No uploads yet.</p>}
          </div>
        </section>
      </div>
    </main>
  )
}

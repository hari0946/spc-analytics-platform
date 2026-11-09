import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, errorMessage, type Page } from '../api/client'

type Row = Record<string, unknown>
interface Field { key: string; label: string; type?: 'text' | 'password' | 'number' | 'ref'; ref?: { path: string; label: string }; required?: boolean }
interface Tab {
  id: string; label: string; path: string; idKey: string; cols: [string, string][]; fields?: Field[]
  build?: (v: Record<string, string>) => Record<string, unknown>; deletable?: boolean; toggle?: boolean; filter?: boolean
}
const csv = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
const numOrNull = (s: string) => (s === '' ? null : Number(s))

const TABS: Tab[] = [
  { id: 'users', label: 'Users', path: '/users', idKey: 'user_id', cols: [['username', 'Username'], ['email', 'Email'], ['roles', 'Roles'], ['is_active', 'Active']],
    fields: [{ key: 'username', label: 'Username', required: true }, { key: 'email', label: 'Email', required: true }, { key: 'password', label: 'Password', type: 'password', required: true }, { key: 'roles', label: 'Roles (comma separated)' }],
    build: (v) => ({ ...v, roles: v.roles ? csv(v.roles) : ['VIEWER'] }), deletable: true, toggle: true },
  { id: 'roles', label: 'Roles', path: '/roles', idKey: 'role_id', cols: [['role_name', 'Role'], ['description', 'Description'], ['permissions', 'Permissions'], ['is_builtin', 'Built-in']],
    fields: [{ key: 'role_name', label: 'Name (UPPER_CASE)', required: true }, { key: 'description', label: 'Description' }, { key: 'permissions', label: 'Permissions (comma separated)' }],
    build: (v) => ({ ...v, permissions: csv(v.permissions ?? '') }), deletable: true },
  { id: 'plants', label: 'Plants', path: '/plants', idKey: 'plant_id', cols: [['plant_code', 'Code'], ['plant_name', 'Name'], ['location', 'Location'], ['is_active', 'Active']],
    fields: [{ key: 'plant_code', label: 'Code', required: true }, { key: 'plant_name', label: 'Name', required: true }, { key: 'location', label: 'Location' }], deletable: true, toggle: true },
  { id: 'lines', label: 'Production lines', path: '/lines', idKey: 'line_id', cols: [['line_code', 'Code'], ['line_name', 'Name'], ['plant_id', 'Plant'], ['is_active', 'Active']],
    fields: [{ key: 'plant_id', label: 'Plant', type: 'ref', ref: { path: '/plants', label: 'plant_code' }, required: true }, { key: 'line_code', label: 'Code', required: true }, { key: 'line_name', label: 'Name', required: true }], deletable: true, toggle: true },
  { id: 'assets', label: 'Assets', path: '/assets', idKey: 'asset_id', cols: [['asset_code', 'Code'], ['asset_name', 'Name'], ['asset_type', 'Type'], ['line_id', 'Line'], ['is_active', 'Active']],
    fields: [{ key: 'line_id', label: 'Line', type: 'ref', ref: { path: '/lines', label: 'line_code' }, required: true }, { key: 'asset_code', label: 'Code', required: true }, { key: 'asset_name', label: 'Name', required: true }, { key: 'asset_type', label: 'Type' }], deletable: true, toggle: true },
  { id: 'tags', label: 'Tags', path: '/tags', idKey: 'tag_id', cols: [['tag_name', 'Tag'], ['parameter', 'Parameter'], ['unit', 'Unit'], ['lsl', 'LSL'], ['usl', 'USL'], ['target', 'Target'], ['asset_id', 'Asset']],
    fields: [{ key: 'asset_id', label: 'Asset', type: 'ref', ref: { path: '/assets', label: 'asset_code' }, required: true }, { key: 'tag_name', label: 'Tag', required: true }, { key: 'parameter', label: 'Parameter', required: true }, { key: 'unit', label: 'Unit' },
      { key: 'lsl', label: 'LSL', type: 'number' }, { key: 'usl', label: 'USL', type: 'number' }, { key: 'target', label: 'Target', type: 'number' }],
    build: (v) => ({ ...v, lsl: numOrNull(v.lsl ?? ''), usl: numOrNull(v.usl ?? ''), target: numOrNull(v.target ?? '') }), deletable: true, toggle: true },
  { id: 'audit', label: 'Audit logs', path: '/audit-logs', idKey: 'audit_id', cols: [['created_at', 'Time (UTC)'], ['actor_username', 'User'], ['action', 'Action'], ['entity_type', 'Entity'], ['success', 'OK'], ['ip_address', 'IP']], filter: true },
]

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/
const show = (v: unknown) => {
  if (v == null) return ''
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  const s = String(v)
  return ISO.test(s) ? s.replace('T', ' ').slice(0, 19) : s       // only real timestamps are reformatted
}

function Panel({ tab }: { tab: Tab }) {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('')
  const [form, setForm] = useState<Record<string, string>>({})
  const [refs, setRefs] = useState<Record<string, Row[]>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Page<Row>>(tab.path, { params: { page, page_size: 15, ...(tab.filter && filter ? { action: filter } : {}) } })
      setRows(data.items); setTotal(data.total)
    } catch (e) { setMsg({ ok: false, text: errorMessage(e) }) }
  }, [tab, page, filter])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    tab.fields?.filter((f) => f.type === 'ref').forEach((f) =>
      api.get<Page<Row>>(f.ref!.path, { params: { page_size: 100 } }).then((r) => setRefs((x) => ({ ...x, [f.key]: r.data.items }))).catch(() => undefined))
  }, [tab])

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null)
    try { await fn(); setMsg({ ok: true, text: ok }); await load() } catch (e) { setMsg({ ok: false, text: errorMessage(e) }) }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const clean = Object.fromEntries(Object.entries(form).filter(([, v]) => v !== ''))
    void act(async () => { await api.post(tab.path, tab.build ? tab.build(clean) : clean); setForm({}) }, 'Created.')
  }
  const pages = Math.max(1, Math.ceil(total / 15))

  return (
    <div className="space-y-4">
      {tab.fields && (
        <form onSubmit={submit} className="rounded-xl bg-white shadow p-4 flex flex-wrap items-end gap-3" aria-label={`Create ${tab.label}`}>
          {tab.fields.map((f) => f.type === 'ref' ? (
            <label key={f.key} className="text-sm">{f.label}
              <select className="mt-1 block rounded border px-2 py-1.5" value={form[f.key] ?? ''} required={f.required} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                <option value="">— select —</option>
                {(refs[f.key] ?? []).map((r) => <option key={r[Object.keys(r)[0]] as string} value={r[Object.keys(r)[0]] as string}>{String(r[f.ref!.label])}</option>)}
              </select></label>
          ) : (
            <label key={f.key} className="text-sm">{f.label}
              <input className="mt-1 block rounded border px-2 py-1.5 w-40" type={f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'} step="any"
                required={f.required} value={form[f.key] ?? ''} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} /></label>
          ))}
          <button className="rounded bg-blue-600 px-4 py-2 text-sm text-white">Add</button>
        </form>
      )}
      {tab.filter && <input className="rounded border px-3 py-1.5 text-sm" placeholder="Filter by action (e.g. LOGIN)" value={filter} onChange={(e) => { setFilter(e.target.value); setPage(1) }} />}
      {msg && <p role={msg.ok ? 'status' : 'alert'} className={`rounded px-3 py-2 text-sm ${msg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{msg.text}</p>}
      <div className="rounded-xl bg-white shadow p-4 overflow-auto">
        <table className="min-w-full text-sm" data-testid={`table-${tab.id}`}>
          <thead><tr className="text-left text-slate-500">{tab.cols.map(([, l]) => <th key={l} className="pr-4">{l}</th>)}<th /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r[tab.idKey] as string} className="border-t">
              {tab.cols.map(([k]) => <td key={k} className="pr-4 py-1">{show(r[k])}</td>)}
              <td className="whitespace-nowrap">
                {tab.toggle && <button className="text-blue-600 hover:underline mr-3" onClick={() => void act(() => api.patch(`${tab.path}/${r[tab.idKey]}`, { is_active: !r.is_active }), 'Updated.')}>{r.is_active ? 'Deactivate' : 'Activate'}</button>}
                {tab.deletable && <button className="text-red-600 hover:underline" onClick={() => window.confirm('Delete this record?') && void act(() => api.delete(`${tab.path}/${r[tab.idKey]}`), 'Deleted.')}>Delete</button>}
              </td>
            </tr>))}</tbody>
        </table>
        {rows.length === 0 && <p className="text-sm text-slate-500 py-2">No records.</p>}
        <div className="flex items-center gap-3 pt-3 text-sm">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded border px-3 py-1 disabled:opacity-40">Previous</button>
          <span>Page {page} of {pages} ({total})</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded border px-3 py-1 disabled:opacity-40">Next</button>
        </div>
      </div>
    </div>
  )
}

export default function Admin() {
  const [id, setId] = useState('users')
  const tab = TABS.find((t) => t.id === id)!
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-6xl space-y-4">
        <header className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">Administration</h1><Link to="/" className="text-sm text-blue-600 hover:underline">← Home</Link>
        </header>
        <nav className="flex flex-wrap gap-2" role="tablist">{TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={t.id === id} onClick={() => setId(t.id)} className={`rounded px-3 py-1.5 text-sm ${t.id === id ? 'bg-blue-600 text-white' : 'bg-white shadow'}`}>{t.label}</button>))}</nav>
        <Panel key={tab.id} tab={tab} />
      </div>
    </main>
  )
}

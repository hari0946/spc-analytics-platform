import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const MODULES: { to: string; label: string; perm?: string; desc: string }[] = [
  { to: '/dashboard', label: 'Overview', desc: 'Process status, KPIs, violations, quality' },
  { to: '/monitoring', label: 'Process monitoring', desc: 'Historical trends: temperature, pressure, speed, flow' },
  { to: '/analysis', label: 'SPC analysis', desc: 'Statistics, control charts, limits, rules, capability, distribution' },
  { to: '/explorer', label: 'Data explorer', desc: 'Filter and browse historical readings' },
  { to: '/uploads', label: 'Data upload', desc: 'Upload and process CSV / Excel history' },
  { to: '/assistant', label: 'AI assistant', desc: 'Ask questions about your data' },
  { to: '/reports', label: 'Reports', desc: 'PDF and Excel reports', perm: 'reports:generate' },
  { to: '/admin', label: 'Administration', desc: 'Users, roles, plants, assets, tags, audit logs', perm: 'users:manage' },
]

export default function Home() {
  const { user, signOut } = useAuth()
  const can = (p?: string) => !p || !!user?.permissions.includes('*') || !!user?.permissions.includes(p)

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="mx-auto max-w-4xl space-y-5">
        <header className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-semibold">SPC Analytics Platform</h1>
            <p className="text-sm text-slate-500">Historical data-based statistical process control</p>
          </div>
          <div className="text-right text-sm">
            <div>Signed in as <strong>{user?.username}</strong> ({user?.roles.join(', ')})</div>
            <button className="text-slate-600 hover:underline" onClick={() => void signOut()}>Sign out</button>
          </div>
        </header>
        <nav className="grid grid-cols-1 sm:grid-cols-2 gap-3" aria-label="Modules">
          {MODULES.filter((m) => can(m.perm)).map((m) => (
            <Link key={m.to} to={m.to} className="rounded-xl bg-white shadow p-4 hover:shadow-md">
              <div className="font-medium text-blue-700">{m.label}</div><div className="text-sm text-slate-500">{m.desc}</div>
            </Link>
          ))}
        </nav>
      </div>
    </main>
  )
}

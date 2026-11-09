import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { errorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const { user, signIn } = useAuth()
  const nav = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(username, password)
      nav('/')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl bg-white shadow p-6 space-y-4">
        <h1 className="text-xl font-semibold">Sign in</h1>
        <label className="block text-sm">Username
          <input className="mt-1 w-full rounded border px-3 py-2" value={username} autoComplete="username"
            onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="block text-sm">Password
          <input className="mt-1 w-full rounded border px-3 py-2" type="password" value={password}
            autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <button disabled={busy} className="w-full rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50">
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}

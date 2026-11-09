import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import * as api from '../api/client'

interface AuthState {
  user: api.Me | null
  loading: boolean
  signIn: (u: string, p: string) => Promise<void>
  signOut: () => Promise<void>
}
const Ctx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<api.Me | null>(null)
  const [loading, setLoading] = useState(!!api.tokenStore.get())

  useEffect(() => {
    if (!api.tokenStore.get()) return
    api.getMe().then(setUser).catch(() => api.tokenStore.clear()).finally(() => setLoading(false))
  }, [])

  const signIn = useCallback(async (u: string, p: string) => {
    const { access_token } = await api.login(u, p)
    api.tokenStore.set(access_token)
    setUser(await api.getMe())
  }, [])

  const signOut = useCallback(async () => {
    try { await api.logout() } catch { /* session may already be gone */ }
    api.tokenStore.clear()
    setUser(null)
  }, [])

  return <Ctx.Provider value={{ user, loading, signIn, signOut }}>{children}</Ctx.Provider>
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth outside AuthProvider')
  return c
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <p className="p-6 text-slate-500">Loading…</p>
  return user ? <>{children}</> : <Navigate to="/login" replace />
}

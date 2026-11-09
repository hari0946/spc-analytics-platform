import axios, { AxiosError } from 'axios'

const TOKEN_KEY = 'spc_token'
export const tokenStore = {
  get: () => {
    try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
  },
  set: (t: string) => { try { sessionStorage.setItem(TOKEN_KEY, t) } catch { /* ignore */ } },
  clear: () => { try { sessionStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ } },
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 120000,
})

api.interceptors.request.use((cfg) => {
  const t = tokenStore.get()
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

api.interceptors.response.use(
  (r) => r,
  (e: AxiosError) => {
    const url = e.config?.url ?? ''
    if (e.response?.status === 401 && !url.includes('/auth/login')) {
      tokenStore.clear()
      if (location.pathname !== '/login') location.assign('/login')
    }
    return Promise.reject(e)
  },
)

/** Human-readable message from the backend's uniform error body. */
export function errorMessage(e: unknown): string {
  const err = e as AxiosError<{ error?: { message?: string; details?: unknown } }>
  const body = err.response?.data?.error
  if (!body) return err.message ?? 'Unexpected error'
  const d = body.details as { missing_columns?: string[] } | unknown[] | null
  if (d && !Array.isArray(d) && typeof d === 'object' && 'missing_columns' in d && d.missing_columns)
    return `${body.message}: ${d.missing_columns.join(', ')}`
  if (Array.isArray(d) && d.length) {
    return `${body.message}: ${(d as { message?: string }[]).map((x) => x.message).filter(Boolean).join('; ')}`
  }
  return body.message ?? 'Request failed'
}

export interface HealthResponse { status: string; service: string; version: string; timestamp: string }
export const getHealth = async (): Promise<HealthResponse> => (await api.get<HealthResponse>('/health')).data

export interface Me { user_id: string; username: string; email: string; full_name: string | null; roles: string[]; permissions: string[] }
export const login = async (username: string, password: string) =>
  (await api.post<{ access_token: string }>('/auth/login', { username, password })).data
export const getMe = async () => (await api.get<Me>('/auth/me')).data
export const logout = async () => { await api.post('/auth/logout') }

export type DatasetType = 'SENSOR' | 'PRODUCTION'
export interface Preview { columns: string[]; rows: (string | null)[][]; suggested_mapping: Record<string, string | string[]>; warnings: string[] }
export interface Upload {
  upload_id: string; file_name: string; file_type: string; dataset_type: DatasetType | null; file_size_bytes: number | null
  status: string; rows_received: number | null; rows_loaded: number | null; empty_rows_skipped: number | null
  duplicate_rows: number | null; error_message: string | null; uploaded_at: string
  column_mapping?: Record<string, string | string[]> | null; preview?: Preview | null
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export const createUpload = async (file: File, datasetType: DatasetType, allowDuplicate: boolean) => {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('dataset_type', datasetType)
  fd.append('allow_duplicate', String(allowDuplicate))
  return (await api.post<Upload>('/uploads', fd)).data
}
export const listUploads = async (page = 1) => (await api.get<Page<Upload>>('/uploads', { params: { page, page_size: 10 } })).data
export const saveMapping = async (id: string, mapping: Record<string, unknown>) =>
  (await api.put<Upload>(`/uploads/${id}/mapping`, { mapping })).data
export const loadUpload = async (id: string) => (await api.post<Upload>(`/uploads/${id}/load`)).data
export interface Report {
  counts: { bronze_rows: number; input_units: number; silver_rows: number; gold_rows: number; rejected_rows: number
    rejected_by_reason: Record<string, number>; duplicates_removed: number }
  checks: { name: string; passed: boolean; detail: string }[]
  all_checks_passed: boolean
  policy: string
  duration_seconds: number
  time_range: { first: string | null; last: string | null }
}
export const processUpload = async (id: string, timestampFormat?: string) =>
  (await api.post<Upload>(`/uploads/${id}/process`, timestampFormat ? { timestamp_format: timestampFormat } : {})).data
export const getUpload = async (id: string) => (await api.get<Upload>(`/uploads/${id}`)).data
export const getReport = async (id: string) => (await api.get<Report>(`/uploads/${id}/report`)).data
export const deleteUpload =async (id: string) => { await api.delete(`/uploads/${id}`) }

import type { ReactElement } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider, RequireAuth } from './auth/AuthContext'
import Admin from './pages/Admin'
import Analysis from './pages/Analysis'
import Assistant from './pages/Assistant'
import Dashboard from './pages/Dashboard'
import Explorer from './pages/Explorer'
import Home from './pages/Home'
import Login from './pages/Login'
import Monitoring from './pages/Monitoring'
import Reports from './pages/Reports'
import Uploads from './pages/Uploads'

const guard = (el: ReactElement) => <RequireAuth>{el}</RequireAuth>

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={guard(<Home />)} />
          <Route path="/dashboard" element={guard(<Dashboard />)} />
          <Route path="/monitoring" element={guard(<Monitoring />)} />
          <Route path="/analysis" element={guard(<Analysis />)} />
          <Route path="/explorer" element={guard(<Explorer />)} />
          <Route path="/uploads" element={guard(<Uploads />)} />
          <Route path="/assistant" element={guard(<Assistant />)} />
          <Route path="/reports" element={guard(<Reports />)} />
          <Route path="/admin" element={guard(<Admin />)} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

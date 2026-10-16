import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import AppLayout from './components/layout/AppLayout'
import LoginPage from './pages/LoginPage'
import TargetsPage from './pages/TargetsPage'
import SinglePlanPage from './pages/SinglePlanPage'
import FieldsPage from './pages/FieldsPage'
import ResultsPage from './pages/ResultsPage'
import MapPage from './pages/MapPage'
import FactorsPage from './pages/FactorsPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/plan-single" replace />} />
        <Route path="plan-single" element={<SinglePlanPage />} />
        <Route path="targets" element={<TargetsPage />} />
        <Route path="fields" element={<FieldsPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="map" element={<MapPage />} />
        <Route path="factors" element={<FactorsPage />} />
      </Route>
    </Routes>
  )
}

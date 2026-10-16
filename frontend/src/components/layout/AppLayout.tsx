import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { useAuthStore } from '@/stores/auth'
import { Map, MapPinned, Target, LogOut, Menu, Wheat, Building2, SlidersHorizontal } from 'lucide-react'

const navItems = [
  { to: '/plan-single', label: 'План: одно хозяйство', icon: Building2 },
  // Скрыто временно: «План: все хозяйства» (/targets). Маршрут жив по прямой ссылке.
  { to: '/fields', label: 'Доступные поля', icon: Map },
  { to: '/map', label: 'Карта плана', icon: MapPinned },
  { to: '/results', label: 'Результаты', icon: Target },
  { to: '/factors', label: 'Факторы СПП', icon: SlidersHorizontal },
]

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const logout = useAuthStore((s) => s.logout)
  const username = useAuthStore((s) => s.username)
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen overflow-hidden bg-stone-50">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-primary-900 transition-transform lg:static lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center gap-3 px-6 py-5">
          <Wheat className="h-8 w-8 text-primary-300" />
          <div>
            <h1 className="text-lg font-bold text-white">Севооборот</h1>
            <p className="text-xs text-primary-300">Эконива</p>
          </div>
        </div>

        <nav className="mt-4 flex-1 space-y-1 px-3">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `sidebar-link ${isActive ? 'sidebar-link-active' : 'sidebar-link-inactive'}`
              }
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-primary-800 px-4 py-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-primary-200">{username || 'user'}</span>
            <button
              onClick={handleLogout}
              className="rounded-lg p-2 text-primary-300 hover:bg-primary-800 hover:text-white"
              title="Выйти"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile header */}
        <header className="flex items-center gap-4 border-b border-stone-200 bg-white px-4 py-3 lg:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="rounded-lg p-2 text-stone-600 hover:bg-stone-100"
          >
            <Menu className="h-5 w-5" />
          </button>
          <h1 className="font-semibold text-stone-800">Севооборот</h1>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-4 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

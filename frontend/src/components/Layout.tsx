// Orchestration Engine - Layout Component
//
// Depends on: hooks/useAuth.tsx
// Used by:    App.tsx

import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

export default function Layout() {
  const { user, logout } = useAuth()

  return (
    <div className="layout">
      <nav className="sidebar">
        <h1>Orchestration</h1>
        <NavLink to="/" className={({ isActive }) => isActive ? 'active' : ''} end>
          Dashboard
        </NavLink>
        <NavLink to="/usage" className={({ isActive }) => isActive ? 'active' : ''}>
          Usage & Budget
        </NavLink>
        <NavLink to="/services" className={({ isActive }) => isActive ? 'active' : ''}>
          Services
        </NavLink>
        {user && (
          <div className="sidebar-user">
            <span className="user-name">{user.display_name}</span>
            <button className="logout-btn" onClick={logout}>Sign Out</button>
          </div>
        )}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}

import { create } from 'zustand'

interface AuthState {
  token: string | null
  username: string | null
  setAuth: (token: string, username: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('auth-token'),
  username: localStorage.getItem('auth-username'),
  setAuth: (token, username) => {
    localStorage.setItem('auth-token', token)
    localStorage.setItem('auth-username', username)
    set({ token, username })
  },
  logout: () => {
    localStorage.removeItem('auth-token')
    localStorage.removeItem('auth-username')
    set({ token: null, username: null })
  },
}))

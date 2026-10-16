// Учитываем базовый путь сборки (vite base). При хостинге под /soilplanner/
// запросы идут на /soilplanner/api, локально — на /api.
const APP_BASE = import.meta.env.BASE_URL.replace(/\/+$/, '')
const BASE_URL = `${APP_BASE}/api`

function redirectToLogin(): void {
  localStorage.removeItem('auth-token')
  window.location.href = `${APP_BASE}/login`
}

class ApiClient {
  private getHeaders(): HeadersInit {
    const headers: HeadersInit = { 'Content-Type': 'application/json' }
    const token = localStorage.getItem('auth-token')
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    return headers
  }

  async get<T>(path: string): Promise<T> {
    const res = await fetch(`${BASE_URL}${path}`, { headers: this.getHeaders() })
    if (res.status === 401) {
      redirectToLogin()
      throw new Error('Unauthorized')
    }
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.status === 401) {
      redirectToLogin()
      throw new Error('Unauthorized')
    }
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }

  async postBlob(path: string, body: unknown): Promise<Blob> {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.blob()
  }

  async login(username: string, password: string): Promise<{ access_token: string }> {
    const res = await fetch(`${BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username, password }),
    })
    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || 'Login failed')
    }
    return res.json()
  }

  async streamOptimization(
    body: unknown,
    onMessage: (data: unknown) => void,
  ): Promise<void> {
    const token = localStorage.getItem('auth-token')
    const res = await fetch(`${BASE_URL}/optimization/run`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })

    if (!res.ok) throw new Error(await res.text())
    if (!res.body) throw new Error('No response body')

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6).trim()
          if (data === '[DONE]') return
          try {
            onMessage(JSON.parse(data))
          } catch {
            // skip malformed JSON
          }
        }
      }
    }
  }
}

export const api = new ApiClient()

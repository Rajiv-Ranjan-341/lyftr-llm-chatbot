// Base URL of the deployed backend. Baked in at build time:
//   - docker-compose passes it as a build arg
//   - Vercel reads it from the project's environment variables
const RAW_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
export const API_BASE_URL = RAW_BASE.replace(/\/+$/, '')

/** Pull a readable message out of the backend's `detail` field. */
function readDetail(body, fallback) {
  const detail = body?.detail
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const first = detail[0]
    if (first?.msg) {
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null
      return field ? `${field}: ${first.msg}` : first.msg
    }
  }
  return fallback
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { 'Content-Type': 'application/json' }
  if (simulateFailure) headers['X-Simulate-Failure'] = simulateFailure

  const body = { session_id: sessionId, message }
  if (systemPrompt && systemPrompt.trim()) body.system_prompt = systemPrompt.trim()

  let response
  try {
    response = await fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError('Could not reach the server. Check your connection and try again.', 0)
  }

  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    const fallback =
      response.status === 503
        ? 'All providers are unavailable right now. Please try again shortly.'
        : `Request failed (HTTP ${response.status}).`
    throw new ApiError(readDetail(payload, fallback), response.status)
  }
  return payload
}

export async function fetchHealth() {
  try {
    const response = await fetch(`${API_BASE_URL}/health`)
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null
  }
}

export async function fetchHistory(sessionId) {
  const response = await fetch(`${API_BASE_URL}/sessions/${encodeURIComponent(sessionId)}/messages`)
  if (!response.ok) return null
  return await response.json()
}

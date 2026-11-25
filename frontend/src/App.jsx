import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, API_BASE_URL, fetchHealth, sendChat } from './api.js'
import MessageList from './components/MessageList.jsx'
import Composer from './components/Composer.jsx'

/** A fresh session id on every page load, as the contract requires. */
function newSessionId() {
  const random =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10)
  return `s-${random}`
}

export default function App() {
  const sessionId = useMemo(newSessionId, [])
  const [messages, setMessages] = useState([])
  const [pending, setPending] = useState(false)
  const [health, setHealth] = useState(null)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [simulateFailure, setSimulateFailure] = useState('')
  const nextId = useRef(0)

  useEffect(() => {
    fetchHealth().then(setHealth)
  }, [])

  const appendMessage = (message) => {
    nextId.current += 1
    setMessages((previous) => [...previous, { ...message, id: nextId.current }])
  }

  const handleSend = async (text) => {
    const trimmed = text.trim()
    if (!trimmed || pending) return

    appendMessage({ role: 'user', content: trimmed })
    setPending(true)

    try {
      const data = await sendChat({
        sessionId,
        message: trimmed,
        systemPrompt,
        simulateFailure,
      })
      appendMessage({
        role: 'assistant',
        content: data.reply,
        metadata: data.metadata,
      })
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : 'Something went wrong. Please try again.'
      appendMessage({
        role: 'assistant',
        kind: 'error',
        content: message,
        status: error instanceof ApiError ? error.status : undefined,
      })
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-title">
          <span className="logo" aria-hidden="true" />
          <div>
            <h1>Lyftr Chat</h1>
            <p className="subtitle">LiteLLM gateway · key failover · response cache</p>
          </div>
        </div>
        <dl className="header-stats">
          <div>
            <dt>Session</dt>
            <dd title={sessionId}>{sessionId}</dd>
          </div>
          <div>
            <dt>Keys</dt>
            <dd>{health ? health.keys_configured : '—'}</dd>
          </div>
          <div>
            <dt>Cache</dt>
            <dd>{health ? (health.cache_enabled ? 'on' : 'off') : '—'}</dd>
          </div>
          <div>
            <dt>Backend</dt>
            <dd className={health?.status === 'ok' ? 'ok' : 'down'}>
              {health?.status === 'ok' ? 'healthy' : health ? health.status : 'unreachable'}
            </dd>
          </div>
        </dl>
      </header>

      <section className="controls">
        <label className="field">
          <span className="field-label">System prompt (optional)</span>
          <input
            data-testid="system-prompt-input"
            type="text"
            value={systemPrompt}
            onChange={(event) => setSystemPrompt(event.target.value)}
            placeholder="You are a concise, helpful assistant."
            autoComplete="off"
          />
        </label>
        <label className="field field-narrow">
          <span className="field-label">
            Simulate failure
            <span className="hint" title="Sent as the X-Simulate-Failure header. Requires ENABLE_FAILURE_SIMULATION=true on the backend.">
              ?
            </span>
          </span>
          <select
            data-testid="simulate-failure-select"
            value={simulateFailure}
            onChange={(event) => setSimulateFailure(event.target.value)}
          >
            <option value="">off</option>
            <option value="key_1=429">key_1=429</option>
            <option value="key_1=500">key_1=500</option>
            <option value="key_1=429,key_2=500">key_1=429,key_2=500</option>
            <option value="*=429">*=429 (all keys)</option>
          </select>
        </label>
      </section>

      <MessageList messages={messages} pending={pending} />

      <Composer onSend={handleSend} disabled={pending} />

      <footer className="footer">
        <span>API: {API_BASE_URL}</span>
        <span>A new session starts on every page load.</span>
      </footer>
    </div>
  )
}

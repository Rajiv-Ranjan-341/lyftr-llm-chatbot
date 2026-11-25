import { useState } from 'react'

export default function Composer({ onSend, disabled }) {
  const [draft, setDraft] = useState('')

  const submit = () => {
    const text = draft.trim()
    if (!text || disabled) return
    // Cleared immediately so the input is empty after sending.
    setDraft('')
    onSend(text)
  }

  const handleKeyDown = (event) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <textarea
        data-testid="chat-input"
        className="composer-input"
        value={draft}
        rows={1}
        maxLength={4000}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask something…  (Enter to send, Shift+Enter for a new line)"
        aria-label="Message"
      />
      <button
        data-testid="send-button"
        className="composer-send"
        type="submit"
        disabled={disabled || !draft.trim()}
      >
        {disabled ? 'Sending…' : 'Send'}
      </button>
    </form>
  )
}

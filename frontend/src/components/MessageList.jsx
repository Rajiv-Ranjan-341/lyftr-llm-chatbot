import { useEffect, useRef } from 'react'

function MetadataRow({ metadata }) {
  const cached = metadata.resolution_source === 'LLM_CACHE'
  return (
    <div className="meta">
      <span
        data-testid="meta-source"
        className={cached ? 'chip chip-cache' : 'chip chip-live'}
        title="Where this reply came from"
      >
        {metadata.resolution_source}
      </span>
      <span className="meta-item">
        <span className="meta-key">model</span>
        <span data-testid="meta-model">{metadata.model}</span>
      </span>
      <span className="meta-item">
        <span className="meta-key">latency</span>
        <span data-testid="meta-latency">{metadata.latency_ms} ms</span>
      </span>
      <span className="meta-item">
        <span className="meta-key">in</span>
        <span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
      </span>
      <span className="meta-item">
        <span className="meta-key">out</span>
        <span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
      </span>
      <span className="meta-item">
        <span className="meta-key">key</span>
        <span data-testid="meta-key-id">{metadata.key_id ?? '—'}</span>
      </span>
      {metadata.fallback_attempts > 0 && (
        <span className="meta-item meta-fallback" title="Keys that failed before this one answered">
          <span className="meta-key">fallbacks</span>
          <span data-testid="meta-fallback-attempts">{metadata.fallback_attempts}</span>
        </span>
      )}
    </div>
  )
}

export default function MessageList({ messages, pending }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, pending])

  return (
    <main className="messages" aria-live="polite">
      {messages.length === 0 && !pending && (
        <div className="empty">
          <p className="empty-title">Start the conversation</p>
          <p className="empty-body">
            Ask the same question twice to watch the second answer come back as{' '}
            <code>LLM_CACHE</code>. Set <em>Simulate failure</em> to <code>key_1=429</code> to make
            the gateway fail over to <code>key_2</code>.
          </p>
        </div>
      )}

      {messages.map((message) => {
        if (message.role === 'user') {
          return (
            <article key={message.id} className="bubble bubble-user">
              <div className="bubble-body">{message.content}</div>
            </article>
          )
        }

        if (message.kind === 'error') {
          // Errors are readable in the chat, but are not tagged as
          // `assistant-message`: they carry no per-request metadata.
          return (
            <article key={message.id} className="bubble bubble-error" role="alert">
              <div className="bubble-role">
                Error{message.status ? ` · HTTP ${message.status}` : ''}
              </div>
              <div className="bubble-body">{message.content}</div>
            </article>
          )
        }

        return (
          <article
            key={message.id}
            data-testid="assistant-message"
            className="bubble bubble-assistant"
          >
            <div className="bubble-body">{message.content}</div>
            {message.metadata && <MetadataRow metadata={message.metadata} />}
          </article>
        )
      })}

      {pending && (
        <article className="bubble bubble-assistant bubble-pending">
          <div className="typing" aria-label="Assistant is typing">
            <span />
            <span />
            <span />
          </div>
        </article>
      )}

      <div ref={endRef} />
    </main>
  )
}

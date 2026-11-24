import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  ""
);

type ResolutionSource = "LIVE_INFERENCE" | "LLM_CACHE";

interface ChatMetadata {
  resolution_source: ResolutionSource;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
}

type ChatMessage =
  | { id: number; role: "user"; content: string }
  | {
      id: number;
      role: "assistant";
      content: string;
      metadata: ChatMetadata;
      simulated: string | null;
    }
  | { id: number; role: "error"; content: string; status: number | null };

interface ChatResponse {
  session_id: string;
  reply: string;
  metadata: ChatMetadata;
}

interface Health {
  status: string;
  keys_configured: number;
  cache_enabled: boolean;
  model?: string;
  failure_simulation?: boolean;
}

const SIMULATIONS = [
  { value: "", label: "Off" },
  { value: "key_1=429", label: "key_1 returns 429" },
  { value: "key_1=500", label: "key_1 returns 500" },
  { value: "key_1=429,key_2=500", label: "key_1 429, key_2 500" },
  { value: "*=429", label: "Every key returns 429" },
];

const STARTERS = [
  "What is the capital of France?",
  "Summarise how HTTP caching works in three sentences.",
  "Write a haiku about rate limits.",
];

class ChatError extends Error {
  constructor(message: string, public status: number | null) {
    super(message);
  }
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `s-${crypto.randomUUID().slice(0, 8)}`;
  }
  return `s-${Math.random().toString(36).slice(2, 10)}`;
}

function describeError(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        typeof d === "object" && d !== null && "msg" in d
          ? String((d as { msg: unknown }).msg)
          : JSON.stringify(d)
      )
      .join("; ");
  }
  return `request failed with status ${status}`;
}

async function postChat(
  sessionId: string,
  message: string,
  systemPrompt: string,
  simulate: string
): Promise<ChatResponse> {
  const payload: Record<string, string> = { session_id: sessionId, message };
  if (systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (simulate) headers["X-Simulate-Failure"] = simulate;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/chat`, { method: "POST", headers, body: JSON.stringify(payload) });
  } catch {
    throw new ChatError(`Can't reach the backend at ${API_BASE}.`, null);
  }
  const data = (await res.json().catch(() => null)) as
    | (ChatResponse & { detail?: unknown })
    | null;
  if (!res.ok || !data) throw new ChatError(describeError(res.status, data?.detail), res.status);
  return data;
}

type KeyStatus = "answered" | "failed" | "not tried" | "skipped (cache)";

function keyStatuses(keys: string[], md: ChatMetadata | null): Record<string, KeyStatus> {
  const out: Record<string, KeyStatus> = {};
  keys.forEach((k) => (out[k] = "not tried"));
  if (!md) return out;
  if (md.resolution_source === "LLM_CACHE") {
    keys.forEach((k) => (out[k] = "skipped (cache)"));
    return out;
  }
  const idx = md.key_id ? keys.indexOf(md.key_id) : -1;
  keys.forEach((k, i) => {
    if (i === idx) out[k] = "answered";
    else if (i < idx && md.fallback_attempts > 0) out[k] = "failed";
  });
  return out;
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [pending, setPending] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  const nextId = useRef(1);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((h: Health) => {
        if (alive) {
          setHealth(h);
          setOnline(true);
        }
      })
      .catch(() => alive && setOnline(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (messages.length || pending) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [input]);

  const keys = useMemo(
    () => Array.from({ length: Math.max(health?.keys_configured ?? 2, 1) }, (_, i) => `key_${i + 1}`),
    [health]
  );

  const assistantMessages = messages.filter(
    (m): m is Extract<ChatMessage, { role: "assistant" }> => m.role === "assistant"
  );
  const selected =
    assistantMessages.find((m) => m.id === selectedId) ??
    assistantMessages[assistantMessages.length - 1] ??
    null;
  const statuses = keyStatuses(keys, selected?.metadata ?? null);

  const startNewSession = () => {
    setSessionId(newSessionId());
    setMessages([]);
    setSelectedId(null);
    setInput("");
    inputRef.current?.focus();
  };

  const send = async () => {
    const text = input.trim();
    if (!text || pending) return;
    const sim = simulate;

    setInput("");
    setMessages((prev) => [...prev, { id: nextId.current++, role: "user", content: text }]);
    setPending(true);
    try {
      const r = await postChat(sessionId, text, systemPrompt, sim);
      const id = nextId.current++;
      setMessages((prev) => [
        ...prev,
        { id, role: "assistant", content: r.reply, metadata: r.metadata, simulated: sim || null },
      ]);
      setSelectedId(id);
    } catch (err) {
      const status = err instanceof ChatError ? err.status : null;
      const detail = err instanceof Error ? err.message : "unexpected error";
      setMessages((prev) => {
        // The server stores nothing for a failed request, so the user turn goes too.
        const rest = prev.length && prev[prev.length - 1].role === "user" ? prev.slice(0, -1) : prev;
        return [...rest, { id: nextId.current++, role: "error", content: detail, status }];
      });
      setInput(text);
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="app">
      <a className="skip" href="#composer-input">
        Skip to message box
      </a>

      <header className="topbar">
        <div className="topbar__brand">
          <svg className="mark" viewBox="0 0 20 20" aria-hidden="true">
            <rect x="1" y="5" width="8" height="10" rx="1.5" />
            <rect x="11" y="5" width="8" height="10" rx="1.5" />
          </svg>
          <span className="topbar__name">Failover Chat</span>
        </div>
        <div className="topbar__status" aria-live="polite">
          <span className={`dot ${online === false ? "dot--down" : online ? "dot--up" : ""}`} />
          {online === null && "Connecting"}
          {online === true && (
            <>
              <span className="mono">{health?.model ?? "model unknown"}</span>
              <span className="sep" aria-hidden="true" />
              {health?.keys_configured ?? 0} keys
              <span className="sep" aria-hidden="true" />
              cache {health?.cache_enabled ? "on" : "off"}
            </>
          )}
          {online === false && "Backend unreachable"}
        </div>
        <button
          type="button"
          className="btn btn--secondary details-toggle"
          aria-expanded={panelOpen}
          aria-controls="inspector"
          onClick={() => setPanelOpen((o) => !o)}
        >
          {panelOpen ? "Close details" : "Details"}
        </button>
        <button type="button" className="btn btn--secondary" onClick={startNewSession}>
          New chat
        </button>
      </header>

      <div className="workspace">
        <main className="chat">
          <div className="thread" aria-live="polite">
            {messages.length === 0 && !pending && (
              <section className="intro">
                <h1>Chat with the model behind a two-key gateway</h1>
                <p>
                  Each reply lists where it came from, the model, the server latency and the
                  tokens for that request. Send the same first message in a new chat to get it
                  from the cache.
                </p>
                <ul className="starters">
                  {STARTERS.map((s) => (
                    <li key={s}>
                      <button
                        type="button"
                        className="starter"
                        onClick={() => {
                          setInput(s);
                          inputRef.current?.focus();
                        }}
                      >
                        {s}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {messages.map((m) => {
              if (m.role === "user") {
                return (
                  <div className="msg msg--user" key={m.id}>
                    <p>{m.content}</p>
                  </div>
                );
              }
              if (m.role === "error") {
                return (
                  <div className="msg msg--error" key={m.id} role="alert">
                    <strong>
                      Request failed{m.status ? ` (${m.status})` : ""}: {m.content}
                    </strong>
                    <span>Nothing was saved to this chat. Your message is back in the box below.</span>
                  </div>
                );
              }
              const md = m.metadata;
              const cached = md.resolution_source === "LLM_CACHE";
              const isSelected = selected?.id === m.id;
              return (
                <article
                  key={m.id}
                  className={`msg msg--assistant ${isSelected ? "is-selected" : ""}`}
                  data-testid="assistant-message"
                >
                  <p className="msg__text">{m.content}</p>
                  <button
                    type="button"
                    className="meta"
                    onClick={() => setSelectedId(m.id)}
                    aria-pressed={isSelected}
                    aria-label="Show request details"
                  >
                    <span
                      className={`tag ${cached ? "tag--cache" : "tag--live"}`}
                      data-testid="meta-source"
                    >
                      {md.resolution_source}
                    </span>
                    <span data-testid="meta-model">{md.model}</span>
                    <span data-testid="meta-latency">{md.latency_ms} ms</span>
                    <span>
                      <span data-testid="meta-input-tokens">{md.input_tokens}</span> in
                    </span>
                    <span>
                      <span data-testid="meta-output-tokens">{md.output_tokens}</span> out
                    </span>
                    {md.fallback_attempts > 0 && (
                      <span className="meta__warn">
                        {md.key_id} after {md.fallback_attempts} failed
                      </span>
                    )}
                  </button>
                </article>
              );
            })}

            {pending && (
              <div className="pending" aria-label="Waiting for the reply">
                <span />
                <span />
                <span />
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form className="composer" onSubmit={onSubmit}>
            {simulate && (
              <p className="composer__note">
                Failure simulation is on: <code>X-Simulate-Failure: {simulate}</code>
              </p>
            )}
            <div className="composer__box">
              <label htmlFor="composer-input" className="visually-hidden">
                Message
              </label>
              <textarea
                id="composer-input"
                data-testid="chat-input"
                ref={inputRef}
                value={input}
                rows={1}
                placeholder="Message the model"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
              />
              <button
                data-testid="send-button"
                type="submit"
                className="btn btn--primary"
                disabled={pending || !input.trim()}
              >
                {pending ? "Sending" : "Send"}
              </button>
            </div>
            <p className="composer__hint">Enter sends. Shift+Enter adds a line.</p>
          </form>
        </main>

        <aside
          id="inspector"
          className={`inspector ${panelOpen ? "is-open" : ""}`}
          aria-label="Request details"
        >
          <section className="block">
            <h2>Request</h2>
            {selected ? (
              <dl className="facts">
                <dt>Source</dt>
                <dd>
                  <span
                    className={`tag ${
                      selected.metadata.resolution_source === "LLM_CACHE" ? "tag--cache" : "tag--live"
                    }`}
                  >
                    {selected.metadata.resolution_source}
                  </span>
                </dd>
                <dt>Model</dt>
                <dd className="mono">{selected.metadata.model}</dd>
                <dt>Latency</dt>
                <dd className="mono">{selected.metadata.latency_ms} ms</dd>
                <dt>Input tokens</dt>
                <dd className="mono">{selected.metadata.input_tokens}</dd>
                <dt>Output tokens</dt>
                <dd className="mono">{selected.metadata.output_tokens}</dd>
                <dt>Key</dt>
                <dd className="mono">{selected.metadata.key_id ?? "none (cache)"}</dd>
                <dt>Failed keys</dt>
                <dd className="mono">{selected.metadata.fallback_attempts}</dd>
                {selected.simulated && (
                  <>
                    <dt>Simulated</dt>
                    <dd className="mono">{selected.simulated}</dd>
                  </>
                )}
              </dl>
            ) : (
              <p className="muted">Send a message to see its metadata here.</p>
            )}
          </section>

          <section className="block">
            <h2>Key pool</h2>
            <ul className="pool">
              {keys.map((k) => (
                <li key={k} className={`pool__row pool__row--${statuses[k].split(" ")[0]}`}>
                  <span className="mono">{k}</span>
                  <span>{statuses[k]}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="block">
            <h2>
              <label htmlFor="simulate">Failure simulation</label>
            </h2>
            <select
              id="simulate"
              className="field"
              value={simulate}
              onChange={(e) => setSimulate(e.target.value)}
            >
              {SIMULATIONS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <p className="muted small">
              Sends the <code>X-Simulate-Failure</code> header with every message while it is on.
              {health?.failure_simulation === false && " This backend has it turned off."}
            </p>
          </section>

          <section className="block">
            <h2>
              <label htmlFor="system-prompt">System prompt</label>
            </h2>
            <textarea
              id="system-prompt"
              data-testid="system-prompt-input"
              className="field"
              rows={4}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Optional. Applies to this chat."
            />
          </section>

          <p className="session mono" title="Session id">
            {sessionId}
          </p>
        </aside>
      </div>
    </div>
  );
}

import { useState, type FormEvent } from 'react';

// Contract with the Q&A backend (see app/README.md):
//   POST {VITE_API_URL}/ask   body: { "question": string }
//   200: { "answer": string, "citations": [{ "label": string, "url": string }] }
interface Reply {
  answer: string;
  citations?: { label: string; url: string }[];
}

const API = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || '';
const EXAMPLES = [
  'Which tests burned solid fuel above 30% oxygen?',
  'What did FLEX find about carbon dioxide as a suppressant?',
  'How does air flow speed change flame spread in BASS-II?',
];

export default function Ask() {
  const [question, setQuestion] = useState('');
  const [reply, setReply] = useState<Reply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!question.trim() || !API) return;
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const res = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim() }),
      });
      if (!res.ok) throw new Error(`The question service answered with HTTP ${res.status}.`);
      setReply((await res.json()) as Reply);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The question service could not be reached.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel narrow">
      <p className="eyebrow"><b>04</b> Questions</p>
      <h2>Ask about the experiments</h2>
      <p className="lede">Answers come from the investigation records and cite the source for every claim.</p>
      {API && (
        <p className="notice">
          A new question can take up to 3 minutes to answer, because the answer is written by a free AI service.
          The three example questions below are answered at once.
        </p>
      )}
      {!API && (
        <p className="notice">
          The question service is not connected yet. Put its address in <code>app/.env</code> as{' '}
          <code>VITE_API_URL</code> and restart the dev server.
        </p>
      )}
      <form onSubmit={submit} className="ask">
        <label htmlFor="q">Your question</label>
        <textarea id="q" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
        <div className="chips">
          {EXAMPLES.map((x) => (
            <button key={x} type="button" onClick={() => setQuestion(x)}>
              {x}
            </button>
          ))}
        </div>
        <button type="submit" className="primary" disabled={busy || !API || !question.trim()}>
          {busy ? 'Asking…' : 'Ask'}
        </button>
      </form>
      {error && (
        <p className="notice error" role="alert">
          {error} Check that the service is running, then ask again.
        </p>
      )}
      {reply && (
        <article className="answer">
          <p>{reply.answer}</p>
          {reply.citations && reply.citations.length > 0 && (
            <>
              <h3>Sources</h3>
              <ul>
                {reply.citations.map((c) => (
                  <li key={c.url}>
                    <a href={c.url} target="_blank" rel="noreferrer">
                      {c.label}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </article>
      )}
    </section>
  );
}

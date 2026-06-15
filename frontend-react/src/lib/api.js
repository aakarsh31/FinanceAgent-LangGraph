// In prod, API is served from the same origin — use relative URLs
// In dev, proxy via vite.config.js forwards to localhost:8000
const BASE = import.meta.env.VITE_API_URL || '';

export const api = {
  analyze: (ticker, timeframe, threadId) =>
    fetch(`${BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, timeframe, thread_id: threadId }),
    }).then(r => r.json()),

  // SSE stream — returns an EventSource.
  // onNode(nodeId, data)  — called after each agent completes
  // onDone(result)        — called with the full /analyze payload when graph pauses
  // onError(detail)       — called on pipeline or network error
  // Returns a cleanup function — call it to close the stream.
  analyzeStream: (ticker, timeframe, threadId, { onNode, onDone, onError }) => {
    const params = new URLSearchParams({ ticker, timeframe, thread_id: threadId });
    const es = new EventSource(`${BASE}/analyze/stream?${params}`);

    es.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }

      if (msg.type === 'node_complete') {
        onNode?.(msg.node, msg.data);
      } else if (msg.type === 'done') {
        es.close();
        onDone?.(msg.result);
      } else if (msg.type === 'error') {
        es.close();
        onError?.(msg.detail || 'Pipeline error');
      }
    };

    es.onerror = () => {
      es.close();
      onError?.('Stream connection lost');
    };

    // Return cleanup so caller can abort early (e.g. component unmount)
    return () => es.close();
  },

  approve: (threadId) =>
    fetch(`${BASE}/approve/${threadId}`, { method: 'POST' }).then(r => r.json()),

  portfolio: () =>
    fetch(`${BASE}/portfolio`).then(r => r.json()),

  orders: () =>
    fetch(`${BASE}/orders`).then(r => r.json()),
};

export const BASE_URL = BASE;
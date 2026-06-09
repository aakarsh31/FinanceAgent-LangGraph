const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const api = {
  analyze: (ticker, timeframe, threadId) =>
    fetch(`${BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, timeframe, thread_id: threadId }),
    }).then(r => r.json()),

  approve: (threadId) =>
    fetch(`${BASE}/approve/${threadId}`, { method: 'POST' }).then(r => r.json()),

  portfolio: () =>
    fetch(`${BASE}/portfolio`).then(r => r.json()),

  orders: () =>
    fetch(`${BASE}/orders`).then(r => r.json()),
};

export const BASE_URL = BASE;
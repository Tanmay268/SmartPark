export function connectLiveEvents(handlers = {}) {
  let active = true;
  let lastVersion = null;
  const baseUrl = process.env.REACT_APP_API_URL || 'http://localhost:5000';
  const isNgrokUrl = /ngrok(-free)?\.app/i.test(baseUrl);
  handlers.onStatus?.({ connected: true });

  async function poll() {
    if (!active) {
      return;
    }
    try {
      const token = localStorage.getItem('token');
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      if (isNgrokUrl) {
        headers['ngrok-skip-browser-warning'] = 'true';
      }
      const response = await fetch(`${baseUrl}/events/latest`, {
        headers
      });
      if (!response.ok) {
        throw new Error('Polling failed');
      }
      const data = await response.json();
      handlers.onStatus?.({ connected: true });
      if (lastVersion !== null && data.stateVersion !== lastVersion && data.appEvent) {
        handlers.onEvent?.({ type: data.appEvent.type, payload: data.appEvent.payload });
      }
      lastVersion = data.stateVersion;
    } catch {
      handlers.onStatus?.({ connected: false });
    } finally {
      if (active) {
        window.setTimeout(poll, 2000);
      }
    }
  }

  poll();

  return () => {
    active = false;
  };
}

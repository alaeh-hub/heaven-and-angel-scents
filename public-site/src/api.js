// Client for the Flask JSON API (same-origin, so the CSRF session cookie rides along).

export class ApiError extends Error {
  constructor(message, status, { fromServer = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    // True when the message came from our own JSON {"error"} body.
    this.fromServer = fromServer;
  }
}

async function request(url, { headers, ...options } = {}) {
  let response;
  try {
    response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: { Accept: 'application/json', ...headers },
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError('Could not reach the server. Check your connection and try again.', 0);
  }

  const isJson = (response.headers.get('Content-Type') || '').includes('application/json');
  const body = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    if (body && body.error) throw new ApiError(body.error, response.status, { fromServer: true });
    const fallback = response.status === 429
      ? 'Too many requests. Please wait a moment and try again.'
      : 'Something went wrong. Please try again in a moment.';
    throw new ApiError(fallback, response.status);
  }
  return body;
}

const apiBase = (slug) => `/partner-portal/${encodeURIComponent(slug)}/api`;

export function fetchPackages(slug, scope, signal) {
  const query = scope && scope !== 'all' ? `?scope=${encodeURIComponent(scope)}` : '';
  return request(`${apiBase(slug)}/packages${query}`, { signal });
}

export function fetchPackage(slug, packageId, signal) {
  return request(`${apiBase(slug)}/packages/${packageId}`, { signal });
}

export function sendInquiry(slug, packageId, fields, csrfToken) {
  return request(`${apiBase(slug)}/packages/${packageId}/inquire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(fields),
  });
}

export function fetchSite(slug, signal) {
  return request(`${apiBase(slug)}/site`, { signal });
}

export function sendGeneralInquiry(slug, fields, csrfToken) {
  return request(`${apiBase(slug)}/inquire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(fields),
  });
}

/**
 * Form POST with the CSRF token. A non-JSON 400 means a stale token, so
 * `refresh()` gets a new one and the send is retried once.
 */
export async function withCsrfRetry(tokenRef, send, refresh) {
  try {
    return await send(tokenRef.current);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 400 || err.fromServer) throw err;
    tokenRef.current = await refresh();
    return send(tokenRef.current);
  }
}

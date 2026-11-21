# Stage 1: build the React partner portal
FROM node:22-alpine AS portal
WORKDIR /build
COPY public-site/package.json public-site/package-lock.json public-site/
RUN cd public-site && npm ci
COPY public-site public-site
RUN cd public-site && npm run build

# Stage 2: Flask app under gunicorn + gevent-websocket (one worker)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Run as an unprivileged user, not root.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=app:app . .
COPY --from=portal --chown=app:app /build/static/public-site static/public-site
# Pre-create uploads/ and backups/ so mounted volumes inherit the app owner.
RUN mkdir -p static/uploads backups && chown app:app static/uploads backups
USER app

EXPOSE 8000
# /healthz checks app + database; the header avoids Talisman's HTTPS 301.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3   CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8000/healthz', headers={'X-Forwarded-Proto': 'https'}), timeout=4)" || exit 1
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", "-w", "1", "wsgi:app", "--bind", "0.0.0.0:8000", "--timeout", "60", "--graceful-timeout", "30", "--access-logfile", "-", "--access-logformat", "%({x-forwarded-for}i)s %(l)s %(u)s %(t)s \"%(r)s\" %(s)s %(b)s \"%(f)s\" \"%(a)s\""]

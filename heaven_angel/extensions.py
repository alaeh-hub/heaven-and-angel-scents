import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

# In-memory limiter and Socket.IO are correct only for a single worker (gunicorn -w 1).
limiter = Limiter(
    key_func=get_remote_address,
    # In-memory storage (resets on restart); set RATELIMIT_STORAGE_URI for Redis.
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

_socketio_origins = os.environ.get("SOCKETIO_CORS_ALLOWED_ORIGINS", "same-origin")
socketio_cors_is_wildcard = _socketio_origins == "*"
if _socketio_origins == "same-origin":
    # Default engineio rule: Origin must match Host (works for domain and LAN, refuses others).
    _socketio_origins = None
elif _socketio_origins != "*":
    _socketio_origins = [o.strip()
                         for o in _socketio_origins.split(",") if o.strip()]

socketio = SocketIO(cors_allowed_origins=_socketio_origins)

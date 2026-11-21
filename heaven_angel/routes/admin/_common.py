"""Shared blueprint `bp`: every admin route module registers on it."""
from flask import Blueprint

bp = Blueprint("admin", __name__, url_prefix="/admin")

# The head-office branch: HQ's own stock lives under this branch id.
HQ_BRANCH_ID = 1

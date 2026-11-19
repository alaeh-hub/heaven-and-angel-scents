"""Admin (HQ) blueprint, split by feature.

Importing each module below registers its routes on the shared `bp`.
"""
from heaven_angel.routes.admin._common import bp

from heaven_angel.routes.admin import (  # noqa: F401  (imported for route registration)
    announcements,
    backup,
    batch_formulas,
    branches,
    clear_data,
    dashboard,
    formulas,
    inquiries,
    logs,
    materials,
    packages,
    partners,
    process_flow,
    production,
    products,
    reports,
    sales,
    stock_requests,
    tools,
    users,
)

__all__ = ["bp"]

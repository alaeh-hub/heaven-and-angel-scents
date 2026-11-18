"""Tools the AI assistant can call (Gemini function calling), instead of
dumping a full data snapshot into the prompt on every turn. Each tool is:

  - Scoped server-side by role/branch (ctx["role"], ctx["branch_id"]) —
    the model never gets to choose whose data it reads. Branch staff
    cannot pass a branch_name that isn't their own; it's silently
    overridden, never trusted from the model's arguments.
  - Admin-only tools (HQ catalog/supply/production/partner data — see
    registry._ADMIN_ONLY) are only declared to an Admin session AND
    re-checked in dispatch(), so a Branch session can't reach one even if
    the model names a tool it was never offered.
  - Read-only, EXCEPT propose_stock_request, which writes a draft to
    ai_stock_drafts / ai_stock_draft_items with status 'Pending Review'.
    A human still has to approve it (see the /ai/drafts routes in ai.py)
    before it becomes a real delivery HQ can dispatch: the agent can
    *draft*, never *commit*.
  - Bulk and refill sales (SKU '<code>-BULK', see sale_catalog; there are no
    BULK products any more) are in mL, never bottles, and bulk stock is the
    mL left in bulk_batches — every tool keeps them apart (bulk_ml /
    stock_unit) instead of adding them to bottles.
  - Oil is stocked per scent (scent_oils); get_scent_oils reports it.

Layout: stock.py (stock, deliveries, discrepancies, the draft proposal),
sales.py (sales, credit, customers, financials), catalog.py (HQ catalog,
supply and partner data), declarations.py (what the model is told each
tool does), registry.py (dispatch).
"""
from heaven_angel.routes.ai_tools.declarations import get_tool_declarations
from heaven_angel.routes.ai_tools.registry import dispatch

__all__ = ["dispatch", "get_tool_declarations"]

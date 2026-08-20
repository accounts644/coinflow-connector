#!/usr/bin/env python3
"""
Sweet Sweeps PSP MCP Server - Railway HTTP/SSE edition
Runs as a hosted server so all Cowork users connect to one central instance.

Coinflow API keys:
  COINFLOW_API_KEY      - Chargeback/dispute access
  COINFLOW_VIEW_API_KEY - Read-only view access: payments, customers, balance, reports

Breeze API key:
  BREEZE_API_KEY        - Full access to Breeze disputes, payments, and customers
"""

import os
import json
import asyncio
import base64
import requests

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

# -- Coinflow config -----------------------------------------------------------
API_KEY        = os.environ.get("COINFLOW_API_KEY", "")
VIEW_API_KEY   = os.environ.get("COINFLOW_VIEW_API_KEY", "")
CF_BASE_URL    = "https://api.coinflow.cash/api"

# -- Breeze config -------------------------------------------------------------
BREEZE_API_KEY = os.environ.get("BREEZE_API_KEY", "")
BRZ_BASE_URL   = "https://api.breeze.cash/v1"

if not API_KEY:
    raise RuntimeError("COINFLOW_API_KEY environment variable is not set.")

if not VIEW_API_KEY:
    print("WARNING: COINFLOW_VIEW_API_KEY not set - Coinflow view tools will be unavailable.")

if not BREEZE_API_KEY:
    print("WARNING: BREEZE_API_KEY not set - Breeze tools will be unavailable.")

server = Server("coinflow-connector")


# -- API helpers --------------------------------------------------------------

def cf_get(path: str, params: dict = None) -> dict:
    """Coinflow chargeback API key - dispute/chargeback endpoints."""
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{CF_BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def cf_view_get(path: str, params: dict = None) -> dict:
    """Coinflow view API key - read-only merchant/payment/customer endpoints."""
    if not VIEW_API_KEY:
        raise RuntimeError("COINFLOW_VIEW_API_KEY is not configured on this server.")
    headers = {"Authorization": VIEW_API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{CF_BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# -- Security: secret redaction ----------------------------------------------

# Field names whose values are credentials/secrets and must never leave this
# server. Matched case-insensitively against dict keys at any nesting depth.
SENSITIVE_KEY_PARTS = (
    "apikey", "apikeys", "privateuuid", "publicuuid",
    "secretkey", "secret", "webhookvalidationkey", "validationkey",
    "feepayer", "usdcpayer", "password", "token",
    "privatekey", "signingkey", "clientsecret",
)

# Keys that hold an entire credential bundle - drop the whole subtree.
SENSITIVE_SUBTREES = ("checkbooksettings", "wallets", "shift4merchantargs")


def _is_sensitive(key: str) -> bool:
    k = key.lower().replace("_", "")
    return any(part in k for part in SENSITIVE_KEY_PARTS)


def redact_secrets(obj):
    """Recursively strip credentials from an API response before returning it.

    Coinflow's GET /merchant returns the full merchant config, which embeds
    live processor secrets, hashed API key material and webhook signing keys.
    None of that is needed for dispute work, so it is removed here rather than
    being surfaced to the MCP client.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            kl = key.lower().replace("_", "")
            if kl in SENSITIVE_SUBTREES:
                out[key] = "[REDACTED]"
            elif _is_sensitive(key):
                if isinstance(value, list):
                    out[key] = f"[REDACTED: {len(value)} entries]"
                else:
                    out[key] = "[REDACTED]"
            else:
                out[key] = redact_secrets(value)
        return out
    if isinstance(obj, list):
        return [redact_secrets(item) for item in obj]
    return obj


def brz_get(path: str, params: dict = None) -> dict:
    """Breeze API - Basic auth (api_key as username, empty password)."""
    if not BREEZE_API_KEY:
        raise RuntimeError("BREEZE_API_KEY is not configured on this server.")
    token = base64.b64encode(f"{BREEZE_API_KEY}:".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    r = requests.get(f"{BRZ_BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# -- Tool definitions ---------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [

        # -- Chargeback API tools (existing) ----------------------------------

        Tool(
            name="coinflow_get_payment",
            description="Get full details of a Coinflow payment by its Payment ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "payment_id": {"type": "string", "description": "The Coinflow Payment ID"}
                },
                "required": ["payment_id"]
            }
        ),
        Tool(
            name="coinflow_get_chargeback",
            description="Get chargeback/dispute details for a payment by its Payment ID. Returns reason code, respond-by deadline, and protection decision.",
            inputSchema={
                "type": "object",
                "properties": {
                    "payment_id": {"type": "string", "description": "The Coinflow Payment ID"}
                },
                "required": ["payment_id"]
            }
        ),
        Tool(
            name="coinflow_get_all_chargebacks",
            description="List all open chargebacks/disputes for the Sweet Sweeps merchant account. Optionally filter by status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional filter: CHARGEBACK, CHARGEBACK_WON, or CHARGEBACK_LOST",
                        "enum": ["CHARGEBACK", "CHARGEBACK_WON", "CHARGEBACK_LOST"]
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results to return (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="coinflow_get_customer_history",
            description="Get purchase history for a customer by their Coinflow User ID (UUID format).",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The Coinflow User ID (UUID)"}
                },
                "required": ["customer_id"]
            }
        ),

        # -- View API tools (new) ----------------------------------------------

        Tool(
            name="coinflow_get_all_payments",
            description=(
                "Get all payments for the Sweet Sweeps merchant account. "
                "Supports optional filters: status (completed/pending/failed/refunded), "
                "startDate, endDate (ISO 8601), and limit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by payment status (e.g. completed, pending, failed, refunded)"
                    },
                    "startDate": {
                        "type": "string",
                        "description": "Start of date range in ISO 8601 format (e.g. 2026-06-01T00:00:00Z)"
                    },
                    "endDate": {
                        "type": "string",
                        "description": "End of date range in ISO 8601 format (e.g. 2026-06-30T23:59:59Z)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results to return (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="coinflow_get_wallet_balance",
            description="Get the current Coinflow wallet balance for the Sweet Sweeps merchant account.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="coinflow_get_merchant_info",
            description=(
                "Get merchant account details for Sweet Sweeps including configuration, "
                "settings, and account status. Credentials (API keys, processor secrets, "
                "webhook signing keys, wallet key material) are redacted server-side and "
                "returned as [REDACTED]."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="coinflow_get_customer",
            description="Get profile and account details for a specific customer by their Coinflow User ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The Coinflow User ID (UUID)"
                    }
                },
                "required": ["customer_id"]
            }
        ),
        Tool(
            name="coinflow_get_all_withdrawals",
            description="Get all payout/withdrawal records for the Sweet Sweeps merchant account. Optionally filter by limit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results to return (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="coinflow_get_refunds",
            description="Get all refunds processed for the Sweet Sweeps merchant account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results to return (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="coinflow_get_chargeback_stats",
            description=(
                "Get aggregated chargeback statistics for a given time period. "
                "Returns total count, needs-response count, responded count, total "
                "disputed amount, and breakdowns by status, reason code and card "
                "network. Aggregated by this connector from the chargebacks list - "
                "Coinflow has no server-side stats endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "startDate": {
                        "type": "string",
                        "description": "Start of date range in ISO 8601 format (e.g. 2026-01-01T00:00:00Z)"
                    },
                    "endDate": {
                        "type": "string",
                        "description": "End of date range in ISO 8601 format (e.g. 2026-06-30T23:59:59Z)"
                    }
                }
            }
        ),

        # -- Breeze API tools --------------------------------------------------

        Tool(
            name="breeze_list_disputes",
            description=(
                "List all Breeze disputes for Sweet Sweeps. "
                "The Breeze API returns all disputes in a flat list - each entry includes: "
                "id, createdAt (Unix ms), email, paymentPageId, livemode. "
                "Use breeze_get_payment to fetch full details (amount, package, reason) for each dispute. "
                "Note: Breeze has a 10-day evidence deadline. Evidence is submitted manually via the Breeze dashboard."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of disputes to return (default 50)",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="breeze_get_payment",
            description=(
                "Get details of a Breeze payment page by its ID. "
                "Returns the purchase amount, currency, description (package contents), "
                "customer reference, and payment status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "payment_id": {
                        "type": "string",
                        "description": "The Breeze payment page ID"
                    }
                },
                "required": ["payment_id"]
            }
        ),
        Tool(
            name="breeze_get_customer",
            description=(
                "Get Breeze customer details by customer ID or email. "
                "Returns customer profile, reference ID, and account metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The Breeze customer ID (use this OR email)"
                    },
                    "email": {
                        "type": "string",
                        "description": "Customer email address to search by (use this OR customer_id)"
                    }
                }
            }
        ),
    ]


# -- Tool handlers -------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:

        # -- Chargeback API ----------------------------------------------------

        if name == "coinflow_get_payment":
            data = cf_get(f"/merchant/payments/{arguments['payment_id']}")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_chargeback":
            data = cf_get(f"/merchant/chargebacks/{arguments['payment_id']}")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_all_chargebacks":
            params = {}
            if "status" in arguments:
                params["status"] = arguments["status"]
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            data = cf_get("/merchant/chargebacks", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_customer_history":
            data = cf_get(f"/customers/{arguments['customer_id']}/history")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        # -- View API ----------------------------------------------------------

        elif name == "coinflow_get_all_payments":
            params = {}
            for key in ("status", "startDate", "endDate", "limit"):
                if key in arguments:
                    params[key] = arguments[key]
            data = cf_view_get("/merchant/payments", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_wallet_balance":
            data = cf_view_get("/merchant/balance")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_merchant_info":
            # GET /merchant returns the full merchant config including live
            # processor secrets and hashed API key material. Redact before
            # handing anything back to the client.
            data = redact_secrets(cf_view_get("/merchant"))
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_customer":
            data = cf_view_get(f"/customers/{arguments['customer_id']}")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_all_withdrawals":
            params = {}
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            data = cf_view_get("/merchant/withdraws", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_refunds":
            params = {}
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            data = cf_view_get("/merchant/refunds", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "coinflow_get_chargeback_stats":
            # NOTE: Coinflow has no /merchant/chargebacks/stats endpoint - that
            # path is parsed as /merchant/chargebacks/{paymentId="stats"} and
            # returns 404 "Payment not found". Stats are aggregated here from
            # the chargebacks list instead.
            start_date = arguments.get("startDate")
            end_date = arguments.get("endDate")

            raw = cf_get("/merchant/chargebacks", params={"limit": 1000})
            if isinstance(raw, dict):
                chargebacks = raw.get("data") or raw.get("chargebacks") or []
            else:
                chargebacks = raw or []

            def _in_range(cb):
                ts = cb.get("createdAt") or cb.get("created_at") or ""
                if start_date and str(ts) < start_date:
                    return False
                if end_date and str(ts) > end_date:
                    return False
                return True

            scoped = [cb for cb in chargebacks if _in_range(cb)]

            by_status, by_reason, by_network = {}, {}, {}
            responded = 0
            total_cents = 0

            for cb in scoped:
                status = cb.get("status") or "UNKNOWN"
                by_status[status] = by_status.get(status, 0) + 1

                reason = cb.get("reasonCode") or cb.get("reason") or "UNKNOWN"
                by_reason[reason] = by_reason.get(reason, 0) + 1

                network = cb.get("cardType") or cb.get("network") or "UNKNOWN"
                by_network[network] = by_network.get(network, 0) + 1

                if cb.get("responded"):
                    responded += 1

                amount = cb.get("amount") or {}
                if isinstance(amount, dict):
                    total_cents += amount.get("cents") or 0

            needs_response = sum(
                1 for cb in scoped
                if cb.get("status") == "CHARGEBACK" and not cb.get("responded")
            )

            data = {
                "computed_by": "connector (no upstream stats endpoint)",
                "dateRange": {"startDate": start_date, "endDate": end_date},
                "totalChargebacks": len(scoped),
                "needsResponse": needs_response,
                "responded": responded,
                "totalDisputedAmountUsd": round(total_cents / 100, 2),
                "byStatus": by_status,
                "byReasonCode": by_reason,
                "byCardNetwork": by_network,
            }
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        # -- Breeze API --------------------------------------------------------

        elif name == "breeze_list_disputes":
            # Breeze /disputes endpoint returns all disputes - no server-side status filter supported
            params = {}
            if "limit" in arguments:
                params["limit"] = arguments["limit"]
            data = brz_get("/disputes", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "breeze_get_payment":
            data = brz_get(f"/payment-pages/{arguments['payment_id']}")
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "breeze_get_customer":
            if "customer_id" in arguments:
                data = brz_get(f"/customers/{arguments['customer_id']}")
            elif "email" in arguments:
                data = brz_get("/customers", params={"email": arguments["email"]})
            else:
                return [TextContent(type="text", text="Error: provide either customer_id or email")]
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except requests.HTTPError as e:
        return [TextContent(type="text", text=f"API error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# -- SSE transport setup -------------------------------------------------------

sse_transport = SseServerTransport("/messages/")


async def handle_sse(request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0], streams[1],
            server.create_initialization_options()
        )


async def health(request):
    return JSONResponse({
        "status": "ok",
        "service": "sweetsweeps-psp-connector",
        "coinflow_chargeback_api": bool(API_KEY),
        "coinflow_view_api": bool(VIEW_API_KEY),
        "breeze_api": bool(BREEZE_API_KEY),
    })


async def myip(request):
    try:
        r = requests.get("https://api.ipify.org-format=json", timeout=5)
        ip = r.json().get("ip", "unknown")
    except Exception as e:
        ip = f"error: {str(e)}"
    return JSONResponse({"outbound_ip": ip})


app = Starlette(
    routes=[
        Route("/", endpoint=health),
        Route("/health", endpoint=health),
        Route("/myip", endpoint=myip),
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

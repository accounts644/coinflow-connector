#!/usr/bin/env python3
"""
Coinflow MCP Server for Sweet Sweeps — Railway HTTP/SSE edition
Runs as a hosted server so all Cowork users connect to one central instance.

Two API keys:
  COINFLOW_API_KEY      — Chargeback/dispute access (existing)
  COINFLOW_VIEW_API_KEY — Read-only view access: payments, customers, balance, reports
"""

import os
import json
import asyncio
import requests

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

API_KEY      = os.environ.get("COINFLOW_API_KEY", "")
VIEW_API_KEY = os.environ.get("COINFLOW_VIEW_API_KEY", "")
BASE_URL     = "https://api.coinflow.cash/api"

if not API_KEY:
    raise RuntimeError("COINFLOW_API_KEY environment variable is not set.")

if not VIEW_API_KEY:
    print("WARNING: COINFLOW_VIEW_API_KEY not set — view tools will be unavailable.")

server = Server("coinflow-connector")


# ── API helpers ──────────────────────────────────────────────────────────────

def cf_get(path: str, params: dict = None) -> dict:
    """Chargeback API key — dispute/chargeback endpoints."""
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def cf_view_get(path: str, params: dict = None) -> dict:
    """View API key — read-only merchant/payment/customer endpoints."""
    if not VIEW_API_KEY:
        raise RuntimeError("COINFLOW_VIEW_API_KEY is not configured on this server.")
    headers = {"Authorization": VIEW_API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Tool definitions ─────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [

        # ── Chargeback API tools (existing) ──────────────────────────────────

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

        # ── View API tools (new) ──────────────────────────────────────────────

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
            description="Get merchant account details for Sweet Sweeps including configuration, settings, and account status.",
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
                "Get chargeback statistics for a given time period. "
                "Returns counts and rates by reason code, card network, and outcome."
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
    ]


# ── Tool handlers ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:

        # ── Chargeback API ────────────────────────────────────────────────────

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

        # ── View API ──────────────────────────────────────────────────────────

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
            data = cf_view_get("/merchant")
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
            params = {}
            for key in ("startDate", "endDate"):
                if key in arguments:
                    params[key] = arguments[key]
            data = cf_view_get("/merchant/chargebacks/stats", params=params)
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except requests.HTTPError as e:
        return [TextContent(type="text", text=f"Coinflow API error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# ── SSE transport setup ───────────────────────────────────────────────────────

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
        "service": "coinflow-connector",
        "chargeback_api": bool(API_KEY),
        "view_api": bool(VIEW_API_KEY),
    })


async def myip(request):
    try:
        r = requests.get("https://api.ipify.org?format=json", timeout=5)
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

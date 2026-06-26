#!/usr/bin/env python3
"""
Coinflow MCP Server for Sweet Sweeps — Railway HTTP/SSE edition
Runs as a hosted server so all Cowork users connect to one central instance.
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

API_KEY  = os.environ.get("COINFLOW_API_KEY", "")
BASE_URL = "https://api.coinflow.cash/api"

if not API_KEY:
    raise RuntimeError("COINFLOW_API_KEY environment variable is not set.")

server = Server("coinflow-connector")


def cf_get(path: str, params: dict = None) -> dict:
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
    }
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
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
            description="Get chargeback/dispute details for a payment by its Payment ID.",
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
            description="List all open chargebacks for the Sweet Sweeps merchant account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["CHARGEBACK", "CHARGEBACK_WON", "CHARGEBACK_LOST"]
                    },
                    "limit": {"type": "integer", "default": 50}
                }
            }
        ),
        Tool(
            name="coinflow_get_customer_history",
            description="Get purchase history for a customer by their Coinflow User ID (UUID).",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The Coinflow User ID (UUID)"}
                },
                "required": ["customer_id"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
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

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except requests.HTTPError as e:
        return [TextContent(type="text", text=f"Coinflow API error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# ── SSE transport setup ──────────────────────────────────────────────────────

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
    return JSONResponse({"status": "ok", "service": "coinflow-connector"})


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

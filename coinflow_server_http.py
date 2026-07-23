#!/usr/bin/env python3
"""
Sweet Sweeps PSP MCP Server — Railway HTTP/SSE edition
Runs as a hosted server so all Cowork users connect to one central instance.

Coinflow API keys:
  COINFLOW_API_KEY      — Chargeback/dispute access
  COINFLOW_VIEW_API_KEY — Read-only view access: payments, customers, balance, reports

Breeze API key:
  BREEZE_API_KEY        — Full access to Breeze disputes, payments, and customers
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

# ── Coinflow config ───────────────────────────────────────────────────────────
API_KEY        = os.environ.get("COINFLOW_API_KEY", "")
VIEW_API_KEY   = os.environ.get("COINFLOW_VIEW_API_KEY", "")
CF_BASE_URL    = "https://api.coinflow.cash/api"

# ── Breeze config ─────────────────────────────────────────────────────────────
BREEZE_API_KEY = os.environ.get("BREEZE_API_KEY", "")
BRZ_BASE_URL   = "https://api.breeze.cash/v1"

if not API_KEY:
    raise RuntimeError("COINFLOW_API_KEY environment variable is not set.")

if not VIEW_API_KEY:
    print("WARNING: COINFLOW_VIEW_API_KEY not set — Coinflow view tools will be unavailable.")

if not BREEZE_API_KEY:
    print("WARNING: BREEZE_API_KEY not set — Breeze tools will be unavailable.")

server = Server("coinflow-connector")


# ── API helpers ─────────────────────────────────────────────────────────────

def cf_get(path: str, params: dict = None) -> dict:
    """Coinflow chargeback API key — dispute/chargeback endpoints."""
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{CF_BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def cf_view_get(path: str, params: dict = None) -> dict:
    """Coinflow view API key — read-only merchant/payment/customer endpoints."""
    if not VIEW_API_KEY:
        raise RuntimeError("COINFLOW_VIEW_API_KEY is not configured on this server.")
    headers = {"Authorization": VIEW_API_KEY, "Content-Type": "application/json"}
    r = requests.get(f"{CF_BASE_URL}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def brz_get(path: str, params: dict = None) -> dict:
    """Breeze API — Basic auth (api_key as username, empty password)."""
    if not BREEZE_API_KEY:
        raise RuntimeError("BREEZE_API_KEY is not configured on this server.")
    token = base64.b64encode(f"{BREEZE_API_KEY}:".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    r = requests.get(f"{BRZ_BASE_URL}{path}", headers=headers, params=params, timeout=15)
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

        # ── Breeze API tools ──────────────────────────────────────────────────

        Tool(
            name="breeze_list_disputes",
            description=(
                "List all Breeze disputes for Sweet Sweeps. "
                "The Breeze API returns all disputes in a flat list — each entry includes: "
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
                        "description": "Customer email address to search by (use this OR cH7W7F��W%��B� �ТТТ���Р��2)H)HF�����F�W'2)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ��6W'fW"�6���F����7��2FVb6���F����S�7G"�&wV�V�G3�F�7B���Ɨ7E�FW�D6��FV�EӠ�G'����2)H)H6�&vV&6��)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ���b��R��&6���f��u�vWE���V�B#��FF�6e�vWB�b"��W&6��B���V�G2��&wV�V�G5�w��V�E��Bu��"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�6�&vV&6�#��FF�6e�vWB�b"��W&6��B�6�&vV&6�2��&wV�V�G5�w��V�E��Bu��"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE����6�&vV&6�2#��&�2��Т�b'7FGW2"��&wV�V�G3��&�5�'7FGW2%��&wV�V�G5�'7FGW2%Т�b&Ɩ֗B"��&wV�V�G3��&�5�&Ɩ֗B%��&wV�V�G5�&Ɩ֗B%ТFF�6e�vWB�"��W&6��B�6�&vV&6�2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�7W7F��W%���7F�'�#��FF�6e�vWB�b"�7W7F��W'2��&wV�V�G5�v7W7F��W%��Bu�����7F�'�"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�2)H)Hf�Wr�)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ��VƖb��R��&6���f��u�vWE������V�G2#��&�2��Тf�"�W����'7FGW2"�'7F'DFFR"�&V�DFFR"�&Ɩ֗B"����b�W���&wV�V�G3��&�5��W���&wV�V�G5��W�ТFF�6e�f�Wu�vWB�"��W&6��B���V�G2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�v��WE�&��6R#��FF�6e�f�Wu�vWB�"��W&6��B�&��6R"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE��W&6��E���f�#��FF�6e�f�Wu�vWB�"��W&6��B"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�7W7F��W"#��FF�6e�f�Wu�vWB�b"�7W7F��W'2��&wV�V�G5�v7W7F��W%��Bu��"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE����v�F�G&v�2#��&�2��Т�b&Ɩ֗B"��&wV�V�G3��&�5�&Ɩ֗B%��&wV�V�G5�&Ɩ֗B%ТFF�6e�f�Wu�vWB�"��W&6��B�v�F�G&w2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�&VgV�G2#��&�2��Т�b&Ɩ֗B"��&wV�V�G3��&�5�&Ɩ֗B%��&wV�V�G5�&Ɩ֗B%ТFF�6e�f�Wu�vWB�"��W&6��B�&VgV�G2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&6���f��u�vWE�6�&vV&6��7FG2#��&�2��Тf�"�W����'7F'DFFR"�&V�DFFR"����b�W���&wV�V�G3��&�5��W���&wV�V�G5��W�ТFF�6e�f�Wu�vWB�"��W&6��B�6�&vV&6�2�7FG2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�2)H)H'&VW�R�)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ��VƖb��R��&'&VW�U�Ɨ7E�F�7WFW2#��2'&VW�R�F�7WFW2V�G���B&WGW&�2��F�7WFW2(	B��6W'fW"�6�FR7FGW2f��FW"7W�'FV@�&�2��Т�b&Ɩ֗B"��&wV�V�G3��&�5�&Ɩ֗B%��&wV�V�G5�&Ɩ֗B%ТFF�''��vWB�"�F�7WFW2"�&�3�&�2��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&'&VW�U�vWE���V�B#��FF�''��vWB�b"���V�B�vW2��&wV�V�G5�w��V�E��Bu��"��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�VƖb��R��&'&VW�U�vWE�7W7F��W"#���b&7W7F��W%��B"��&wV�V�G3��FF�''��vWB�b"�7W7F��W'2��&wV�V�G5�v7W7F��W%��Bu��"��VƖb&V���"��&wV�V�G3��FF�''��vWB�"�7W7F��W'2"�&�3ײ&V���#�&wV�V�G5�&V���%�Ґ�V�6S��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C�$W'&�#�&�f�FRV�F�W"7W7F��W%��B�"V���"�Т&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C֧6���GV�2�FF���FV�C�"��Р�V�6S��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C�b%V���v�F��â���W�"�Р�W�6WB&WVW7G2�EEW'&�"2S��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C�b$�W'&�"�R�&W7��6R�7FGW5�6�FWӢ�R�&W7��6R�FW�G�"�ТW�6WBW�6WF���2S��&WGW&��FW�D6��FV�B�G�S�'FW�B"�FW�C�b$W'&�#��7G"�R��"�Р��2)H)H54RG&�7�'B6WGW)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ��76U�G&�7�'B�76U6W'fW%G&�7�'B�"��W76vW2�"����7��2FVb��F�U�76R�&WVW7B���7��2v�F�76U�G&�7�'B�6���V7E�76R��&WVW7B�66�R�&WVW7B�&V6V�fR�&WVW7B��6V�@��27G&V�3��v�B6W'fW"�'V•7G&V�5���7G&V�5����6W'fW"�7&VFU���F�Ɨ�F�����F���2�������7��2FVb�V�F��&WVW7B���&WGW&��4��&W7��6R���'7FGW2#�&��"��'6W'f�6R#�'7vVWG7vVW2�7�6���V7F�""��&6���f��u�6�&vV&6���#�&�����U����&6���f��u�f�Wu��#�&��d�Uu����U����&'&VW�U��#�&��%$TU�U����U����Ґ���7��2FVbז��&WVW7B���G'���"�&WVW7G2�vWB�&�GG3������g���&s�f�&�C֧6��"�F��V�WC�R����"�6�ₒ�vWB�&�"�'V���v�"��W�6WBW�6WF���2S����b&W'&�#��7G"�R�� �&WGW&��4��&W7��6R��&�WF&�V�E��#��Ґ����7F&�WGFR��&�WFW3հ�&�WFR�"�"�V�G���CֆV�F����&�WFR�"��V�F�"�V�G���CֆV�F����&�WFR�"�ז�"�V�G���C�ז����&�WFR�"�76R"�V�G���Cֆ�F�U�76R�����V�B�"��W76vW2�"��76U�G&�7�'B��F�U��7E��W76vR���Т����b����U����%�������#���'B���B��2�V�f�&���vWB�%�%B"�����Wf�6�&��'V����7C�#���"��'C��'B�

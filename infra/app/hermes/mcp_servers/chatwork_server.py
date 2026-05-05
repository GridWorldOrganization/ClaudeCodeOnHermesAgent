"""ChatWork MCP server (stdio transport).

Exposes ChatWork API as MCP tools. Loaded by Hermes Agent at startup
via ~/.hermes/config.yaml mcp_servers entry. Token read from env
CHATWORK_API_TOKEN (injected by entrypoint.sh from Secrets Manager).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

CHATWORK_TOKEN = os.environ.get("CHATWORK_API_TOKEN", "")
BASE_URL = "https://api.chatwork.com/v2"

mcp = FastMCP("chatwork")


def _request(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
    if not CHATWORK_TOKEN:
        raise RuntimeError("CHATWORK_API_TOKEN env var not set")
    url = f"{BASE_URL}{path}"
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"X-ChatWorkToken": CHATWORK_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
        return json.loads(text) if text else None


@mcp.tool()
def send_message(room_id: str, body: str) -> dict[str, Any]:
    """Send a message to a ChatWork room.

    Args:
        room_id: ChatWork room ID (numeric string, visible in the chatwork.com URL)
        body: Message body. Supports ChatWork markup: [info][title]...[/title]...[/info],
              [To:user_id], [code]...[/code], [hr], etc.

    Returns:
        {"message_id": "..."} on success.
    """
    return _request("POST", f"/rooms/{room_id}/messages", {"body": body})


@mcp.tool()
def list_my_rooms() -> list[dict[str, Any]]:
    """List ChatWork rooms accessible to the bot.

    Returns:
        List of {room_id, name, type, role, unread_num, ...} dicts.
    """
    rooms = _request("GET", "/rooms") or []
    return [
        {
            "room_id": r.get("room_id"),
            "name": r.get("name"),
            "type": r.get("type"),
            "role": r.get("role"),
            "unread_num": r.get("unread_num", 0),
        }
        for r in rooms
    ]


@mcp.tool()
def get_room_messages(room_id: str, force: bool = False) -> list[dict[str, Any]]:
    """Get recent messages from a ChatWork room.

    Args:
        room_id: ChatWork room ID
        force: If True, force-fetch even already-read messages

    Returns:
        List of message dicts with {message_id, account, body, send_time, ...}
    """
    suffix = "?force=1" if force else ""
    msgs = _request("GET", f"/rooms/{room_id}/messages{suffix}") or []
    return msgs


if __name__ == "__main__":
    mcp.run()

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
import urllib.error
from pathlib import Path
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


@mcp.tool()
def upload_file(room_id: str, file_path: str, message: str = "") -> dict[str, Any]:
    """Upload a local file to a ChatWork room.

    Args:
        room_id: ChatWork room ID (numeric string).
        file_path: Absolute path to the local file to upload (e.g. /tmp/image_abc.png).
        message: Optional message to attach to the file post.

    Returns:
        {"file_id": ..., "message_id": ...} on success.
    """
    token = CHATWORK_TOKEN
    if not token:
        raise RuntimeError("CHATWORK_API_TOKEN env var not set")

    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    filename = file_path_obj.name
    mime_type = "image/png" if filename.endswith(".png") else "application/octet-stream"

    # Build multipart/form-data manually (no external deps).
    boundary = "----ChatWorkUpload" + os.urandom(8).hex()
    body_parts: list[bytes] = []

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    if message:
        body_parts.append(_field("message", message))

    file_data = file_path_obj.read_bytes()
    body_parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode() + file_data + b"\r\n"
    )
    body_parts.append(f"--{boundary}--\r\n".encode())

    body = b"".join(body_parts)
    req = urllib.request.Request(
        f"{BASE_URL}/rooms/{room_id}/files",
        data=body,
        method="POST",
        headers={
            "X-ChatWorkToken": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    mcp.run()

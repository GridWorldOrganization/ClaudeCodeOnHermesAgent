"""SQS consumer Lambda — ChatWork webhook events → Hermes invoke → ChatWork reply.

Triggered by SQS messages enqueued by chatwork-webhook-handler (Lambda A).
- Filters bot self-messages and disallowed rooms.
- Invokes AgentCore Runtime (Hermes Agent + Claude Sonnet 4.6).
- Posts the response back to the originating ChatWork room.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import uuid

import boto3
from botocore.exceptions import ClientError

# ----- env -----------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]
BOT_ACCOUNT_ID = str(os.environ.get("CHATWORK_BOT_ACCOUNT_ID", ""))
ALLOWED_ROOMS = {
    s.strip()
    for s in os.environ.get("CHATWORK_ALLOWED_ROOM_IDS", "").split(",")
    if s.strip()
}

# ----- ChatWork account_id → display name mapping --------------------------
# Used to build proper [To:<aid>] <Name>さん format in replies.
# Add your team members here: {"account_id": "Display Name"}
# Account IDs are visible in ChatWork URLs or via the API /me endpoint.
CHATWORK_NAMES: dict[str, str] = {
    # "1234567": "Your Name",
}

# ----- aws clients ---------------------------------------------------------
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
sm = boto3.client("secretsmanager", region_name=REGION)

_chatwork_token: str | None = None


def get_chatwork_token() -> str:
    global _chatwork_token
    if _chatwork_token is None:
        r = sm.get_secret_value(SecretId="hermes/chatwork-api-token")
        _chatwork_token = r["SecretString"]
    return _chatwork_token


def post_to_chatwork(room_id: str, body: str) -> dict:
    token = get_chatwork_token()
    data = urllib.parse.urlencode({"body": body}).encode()
    req = urllib.request.Request(
        f"https://api.chatwork.com/v2/rooms/{room_id}/messages",
        data=data,
        headers={"X-ChatWorkToken": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_agentcore_response(raw: bytes) -> str:
    """SSE or JSON response → plain text."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""

    if text.startswith("data:") or "\ndata:" in text:
        chunks = []
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload in ("", "null", "[DONE]"):
                    continue
                try:
                    obj = json.loads(payload)
                    if isinstance(obj, str):
                        chunks.append(obj)
                    elif isinstance(obj, dict):
                        chunks.append(
                            obj.get("text")
                            or obj.get("delta")
                            or obj.get("response")
                            or json.dumps(obj, ensure_ascii=False)
                        )
                    else:
                        chunks.append(str(obj))
                except json.JSONDecodeError:
                    chunks.append(payload)
        return "".join(chunks).strip() or text

    try:
        obj = json.loads(text)
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return (
                obj.get("text")
                or obj.get("response")
                or obj.get("final_response")
                or json.dumps(obj, ensure_ascii=False)
            )
        return json.dumps(obj, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def build_prompt(room_id: str, sender_id: str, body: str) -> str:
    sender_name = CHATWORK_NAMES.get(sender_id, f"account_id={sender_id}")
    return f"""ChatWorkルーム {room_id} で、{sender_name} (account_id={sender_id}) が次のメッセージを投稿しました:

---
{body}
---

このメッセージに対する返答を生成してください。

【ChatWork 投稿フォーマット ルール — 必ず守ること】

新規投稿（返信）の冒頭:
  [To:{sender_id}] {sender_name}さん
  ※ 「]」の直後に半角スペース1つ、次に表示名、直後に「さん」
  ※ 自然文の宛名のみ（「田中さん」等）で書き始めると通知が届かないため禁止

返信形式（相手のメッセージIDがわかる場合）:
  [rp aid={sender_id} to={room_id}-<相手のmessage_id>] {sender_name}さん

【メッセージ品質ルール】
- 返信は3〜5行を目安 (短すぎず長すぎず)
- 絵文字は1〜2個まで
- 「いいですね！」だけの受け身返信禁止 → 自分の意見・補足・関連情報を必ず添える
- 質問は具体的に: 「どうですか？」→「それはいつ頃からですか？」「具体的には何が課題ですか？」
- 同じ話題を3往復以上続けない → 自然に関連トピックへ展開
- 「それで思い出したんだけど…」のような自然なつなぎOK
- 意見が異なる場合は正直に伝える (ただし丁寧に)

【その他のルール】
- ChatWorkルームに投稿されるテキストとして適切なフォーマットで返す (この後 Lambda が自動的に ChatWork API で投稿)
- 返答テキストのみ出力。前置き・メタ説明は不要
- Backlog の情報が必要な場合は backlog 用 MCP ツール (`/app/mcp_servers/backlog_server.py` 内の list_projects, list_issues, get_issue, search_issues 等) を使って取得
- 投稿には ChatWork マークアップ ([info][title]…[/title]…[/info], [code]…[/code], [quote]…[/quote], [hr] 等) を活用してOK
- bot 自身は account_id={BOT_ACCOUNT_ID} なので、自分の発言には絶対に反応しない"""


def handle_record(record: dict) -> None:
    raw_body = record.get("body", "{}")
    try:
        msg = json.loads(raw_body)
    except json.JSONDecodeError:
        print(f"skip: invalid JSON in SQS body: {raw_body[:200]}")
        return

    event_type = str(msg.get("webhook_event_type", ""))
    room_id = str(msg.get("room_id", ""))
    sender_id = str(msg.get("sender_account_id", ""))
    body = msg.get("body", "")

    print(f"[recv] type={event_type} room={room_id} from={sender_id} body_len={len(body)}")

    # message creation events only.
    if event_type and event_type not in ("message_created", "mention_to_me", "to_me"):
        print(f"skip: event_type={event_type} not handled")
        return

    if not body or not body.strip():
        print("skip: empty body")
        return

    if BOT_ACCOUNT_ID and sender_id == BOT_ACCOUNT_ID:
        print(f"skip: bot self message (sender={sender_id})")
        return

    if ALLOWED_ROOMS and room_id not in ALLOWED_ROOMS:
        print(f"skip: room {room_id} not in allowed list {ALLOWED_ROOMS}")
        return

    # Invoke AgentCore Runtime (Hermes + Claude Sonnet 4.6).
    prompt = build_prompt(room_id, sender_id, body)
    session_id = f"chatwork-{room_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    print(f"[invoke] runtime={RUNTIME_ARN.split('/')[-1]} session={session_id}")

    t0 = time.time()
    try:
        res = agentcore.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps(
                {"prompt": prompt, "channel": "chatwork", "chatId": room_id}
            ).encode(),
        )
        body_obj = res.get("response")
        raw = body_obj.read() if hasattr(body_obj, "read") else (body_obj or b"")
        response_text = parse_agentcore_response(raw).strip()
        print(f"[invoke] {time.time()-t0:.1f}s response_len={len(response_text)}")
    except ClientError as e:
        err = e.response.get("Error", {})
        response_text = (
            f"[info][title]⚠️ AgentCore 呼出エラー[/title]"
            f"{err.get('Code','?')}: {err.get('Message', str(e))[:500]}[/info]"
        )
        print(f"[error] AgentCore: {e}")

    if not response_text:
        response_text = "[info]Hermes Agentから空の応答が返ってきました。[/info]"

    # Post to ChatWork.
    try:
        result = post_to_chatwork(room_id, response_text)
        print(f"[posted] message_id={result.get('message_id')}")
    except Exception as e:
        print(f"[fail] ChatWork post: {e}")
        raise  # SQS will retry


def lambda_handler(event, context):
    records = event.get("Records", [])
    print(f"[batch] {len(records)} record(s)")
    for record in records:
        try:
            handle_record(record)
        except Exception as e:
            print(f"[error] record handling: {e}")
            raise  # let SQS retry the failing message
    return {"statusCode": 200, "processed": len(records)}

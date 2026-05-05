#!/usr/bin/env python3
"""Hermes AWS Bedrock Chat Client.

ローカル PC ターミナルから AWS 上の Hermes Agent (Claude on Bedrock) と対話。
session-id で会話継続。/exit /quit で終了。
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# 環境変数読み込み
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        # ${HERMES_HOME} 等の展開
        v_expanded = os.path.expandvars(v.strip())
        os.environ.setdefault(k.strip(), v_expanded)


SCRIPT_DIR = Path(__file__).resolve().parent
load_env_file(SCRIPT_DIR / ".env")

REGION = os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-1"
RUNTIME_ARN = (
    os.environ.get("AGENTCORE_RUNTIME_ARN")
    or ""  # Set AGENTCORE_RUNTIME_ARN env var or update cdk.json after deploy
)

# ---------------------------------------------------------------------------
# カラー
# ---------------------------------------------------------------------------

RED = "\033[0;31m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[0;33m"
GRAY = "\033[0;90m"
BOLD = "\033[1m"
NC = "\033[0m"

# ---------------------------------------------------------------------------
# クライアント
# ---------------------------------------------------------------------------


def new_session() -> str:
    # AWS の runtimeSessionId は 33 文字以上必須。
    return f"hermes-{int(time.time())}-{uuid.uuid4().hex}"


def parse_response(raw: bytes) -> str:
    """invoke_agent_runtime の response body を文字列に整形."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "(empty response)"

    # SSE 形式: "data: ..." 行を抽出
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
                        chunks.append(obj.get("text") or obj.get("delta") or json.dumps(obj, ensure_ascii=False))
                    else:
                        chunks.append(str(obj))
                except json.JSONDecodeError:
                    chunks.append(payload)
        return "".join(chunks) or text

    # JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return obj.get("text") or obj.get("response") or obj.get("final_response") or json.dumps(obj, indent=2, ensure_ascii=False)
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def invoke(client, session_id: str, prompt: str) -> tuple[str, float]:
    t0 = time.time()
    res = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt, "channel": "cli"}).encode(),
    )
    body_obj = res.get("response")
    raw = body_obj.read() if hasattr(body_obj, "read") else (body_obj or b"")
    return parse_response(raw), time.time() - t0


def banner(session_id: str) -> None:
    runtime_id = RUNTIME_ARN.split("/")[-1]
    print(f"{GREEN}{BOLD}=== Hermes Chat Client (AWS Bedrock) ==={NC}")
    print(f"{GRAY}region : {REGION}")
    print(f"runtime: {runtime_id}")
    print(f"session: {session_id}")
    print(f"commands: /exit /quit /new /id /help{NC}")
    print()


def help_text() -> None:
    print(f"{GRAY}/exit /quit  終了")
    print(f"/new         新規セッション開始（会話リセット）")
    print(f"/id          現在の session-id 表示")
    print(f"/help        ヘルプ表示{NC}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    # 認証情報チェック
    try:
        sts = boto3.client("sts", region_name=REGION)
        ident = sts.get_caller_identity()
        account = ident.get("Account", "?")
    except NoCredentialsError:
        print(f"{RED}AWS credentials なし。.env で AWS_ACCESS_KEY_ID/SECRET 設定要{NC}")
        return 2
    except ClientError as e:
        print(f"{RED}AWS auth error: {e}{NC}")
        return 2

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    session_id = new_session()
    banner(session_id)
    print(f"{GRAY}AWS account: {account}{NC}\n")

    while True:
        try:
            user = input(f"{BLUE}{BOLD}You> {NC}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{GRAY}Bye{NC}")
            return 0

        if not user:
            continue

        if user in ("/exit", "/quit"):
            print(f"{GRAY}Bye{NC}")
            return 0
        if user == "/new":
            session_id = new_session()
            print(f"{GRAY}new session: {session_id}{NC}\n")
            continue
        if user == "/id":
            print(f"{GRAY}session-id: {session_id}{NC}\n")
            continue
        if user == "/help":
            help_text()
            print()
            continue

        print(f"{YELLOW}{GRAY}(invoking, first call may take 30-60s for cold start...){NC}")
        try:
            response, elapsed = invoke(client, session_id, user)
            # カーソルを上の行に戻して上書き
            sys.stdout.write("\033[F\033[K")
            print(f"{GREEN}{BOLD}Hermes>{NC} {response}")
            print(f"{GRAY}({elapsed:.1f}s){NC}\n")
        except ClientError as e:
            sys.stdout.write("\033[F\033[K")
            print(f"{RED}AWS error: {e.response.get('Error', {}).get('Code', '?')} - {e.response.get('Error', {}).get('Message', str(e))}{NC}\n")
        except Exception as e:
            sys.stdout.write("\033[F\033[K")
            print(f"{RED}Error: {e}{NC}\n")


if __name__ == "__main__":
    sys.exit(main())

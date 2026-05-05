# 開発引き継ぎ メモ (2026-05-05 → 後続セッション)

## 現状

- Phase 1 (Foundation) ✅ デプロイ済み（東京リージョン）
- Phase 2 (Hermes コンテナ + AgentCore Runtime) ✅ READY
- Anthropic ユースケース申請 ✅ Submit 完了
- AWS Marketplace IAM 権限 ✅ 追加済み
- AgentCore Runtime コンテナ ✅ 強制再起動で credentials refresh 済み
- ローカル CLI `./hermes-chat` ✅ 対話 動作確認済み（2026-05-05 22:00 JST）

## 残タスク 3件

### Task A: Phase 3 Router Lambda + API Gateway デプロイ

#### 内容
公開 webhook URL を作成し、外部サービスから AgentCore を叩けるようにする。

#### 作成されるリソース
- API Gateway HTTP API (パブリックエンドポイント)
- Router Lambda (Python 3.13)
- DynamoDB Identity Table
- Cron Lambda (EventBridge Scheduler 用)
- Token Monitoring Stack (CloudWatch カスタムメトリクス)

#### 月額追加コスト
- Lambda: 無料枠 (100万 リク/月)
- API Gateway: 無料枠 12 ヶ月 (100万 リク/月)
- DynamoDB: 無料枠
- 合計 **約 ¥0 増加**（無料枠内）

#### 実行コマンド

```bash
cd sample-aws
source .venv/bin/activate
set -a; source ../.env; set +a
./scripts/deploy.sh phase3
```

時間: 3〜5 分

#### 動作確認

```bash
# API URL 取得
aws cloudformation describe-stacks \
  --stack-name hermes-agentcore-router \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text

# health チェック
curl https://xxx.execute-api.ap-northeast-1.amazonaws.com/health
```

---

### Task B: ChatWork 連携 改造（Phase 3 後）

#### 設計

```
[ChatWork] webhook (HMAC-SHA256 署名付き)
    │ POST
    ▼
[API Gateway] /webhook/chatwork
    │
    ▼
[Router Lambda] _handle_chatwork(event)
    │ 1. HMAC 署名検証 (X-ChatWorkWebhookSignature)
    │ 2. event.body から message extract
    │ 3. AgentCore invoke
    │ 4. ChatWork API でメッセージ送信
    ▼
[ChatWork ルーム] にエージェント返信
```

#### 改造箇所 (4 ファイル)

##### 1. `sample-aws/stacks/router_stack.py`

```python
routes = [
    ("/webhook/telegram", [apigwv2.HttpMethod.POST]),
    ("/webhook/slack", [apigwv2.HttpMethod.POST]),
    ("/webhook/discord", [apigwv2.HttpMethod.POST]),
    ("/webhook/feishu", [apigwv2.HttpMethod.POST]),
    ("/webhook/chatwork", [apigwv2.HttpMethod.POST]),  # ← 追加
    ("/health", [apigwv2.HttpMethod.GET]),
]
```

##### 2. `sample-aws/lambda/router/index.py`

`_handle_chatwork(event)` 関数追加:

```python
def _handle_chatwork(event: dict) -> dict:
    body = _parse_body(event)
    
    # HMAC 署名検証
    signature = event.get("headers", {}).get("x-chatworkwebhooksignature", "")
    secret = _get_secret("hermes/chatwork-webhook-token")
    expected = hmac.new(secret.encode(), event["rawBody"].encode(),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(signature, base64.b64encode(expected).decode()):
        return _ok({"error": "invalid signature"}, status=401)
    
    # メッセージ抽出
    event_type = body.get("webhook_event_type")
    if event_type not in ("mention_to_me", "message_created"):
        return _ok({"status": "ignored"})
    
    msg = body["webhook_event"]
    room_id = msg["room_id"]
    user_id = msg["from_account_id"]
    message_text = msg["body"]
    
    # AgentCore invoke
    response = _invoke_agentcore(
        prompt=message_text,
        channel="chatwork",
        chat_id=str(room_id),
    )
    
    # ChatWork API でメッセージ送信
    chatwork_token = _get_secret("hermes/chatwork-api-token")
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.chatwork.com/v2/rooms/{room_id}/messages",
            data=urllib.parse.urlencode({"body": response}).encode(),
            headers={
                "X-ChatWorkToken": chatwork_token,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
    )
    
    return _ok({"status": "sent"})

# handler 内に分岐追加
elif path.startswith("/webhook/chatwork"):
    return _handle_chatwork(event)
```

##### 3. `sample-aws/stacks/security_stack.py`

ChatWork 用 Secret 追加:

```python
secret_names = [
    "telegram-bot-token",
    "slack-bot-token",
    "slack-signing-secret",
    "discord-bot-token",
    "feishu-app-id",
    "feishu-app-secret",
    "chatwork-api-token",       # ← 追加
    "chatwork-webhook-token",   # ← 追加
]
```

##### 4. ChatWork 側設定 (手動)

1. ChatWork API トークン取得: https://www.chatwork.com/service/packages/chatwork/subpackages/api/token.php
2. webhook 設定: ルーム or 個人 → webhook URL に `https://xxx.execute-api.ap-northeast-1.amazonaws.com/webhook/chatwork` 設定
3. webhook シークレット取得（HMAC 検証用）
4. Secrets Manager に登録:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id hermes/chatwork-api-token \
     --secret-string "<TOKEN>"
   aws secretsmanager put-secret-value \
     --secret-id hermes/chatwork-webhook-token \
     --secret-string "<WEBHOOK_SECRET>"
   ```

#### デプロイ手順

```bash
# security stack 更新（Secrets 追加）
cd sample-aws
source .venv/bin/activate
cdk deploy hermes-agentcore-security --require-approval never

# Secrets 値設定（上記 ChatWork 側設定 4. 参照）

# router stack 更新（ChatWork ルート追加）
cdk deploy hermes-agentcore-router --require-approval never

# 動作確認: ChatWork から bot に話しかけ → 返答確認
```

#### テスト

```bash
# 自分宛にテストメッセージ → 自動応答確認
# ChatWork ルームで「[To:botId] こんにちは」
```

---

### Task C: IAM Access Key ローテート（セキュリティ対応）

#### 背景
2026-05-05 のセッション中、`.env` 全文を Read した時に Access Key と Secret が会話ログに平文で残った。

漏洩想定で対処:
1. AWS Console → IAM → ユーザー `claude-code-aws-api-mcp`
2. セキュリティ認証情報 → 既存アクセスキー **無効化** → 削除
3. 新規アクセスキー発行
4. `.env` を新キーで上書き
5. 新リポジトリでも `.env` を新キーで作成

漏洩 IAM ユーザーの権限内でできた被害:
- Bedrock 利用料の追加課金（既に課金されている分は決着済）
- 既存リソースの参照・改変
- 主に CloudFormation 経由でのみ操作 → 監査ログあり

優先度: 中（即実害ないが早めに）

---

## 必要な環境（新セッション開始時）

### 1. .env ファイル

旧ディレクトリからコピー（手動推奨、ターミナルで）:

```bash
cp /Users/tobisako/dev/claude_code/gj-board-management/ceo-office/HermesAgent/.env \
   /Users/tobisako/dev/claude_code/dev/ClaudeCodeOnHermesAgent/.env
```

`.gitignore` に `.env` 入っているので git 追跡されない。

または `.env.example` から新規作成。

### 2. Python venv

```bash
cd sample-aws
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. その他

- AWS CLI v2 (2.34+)
- aws-cdk 2.150+ (グローバル install)
- Node.js 18+
- Colima (Apple Silicon Docker)
- AgentCore CLI (`npm i -g @aws/agentcore`)

詳細: [DEPLOYMENT.md](DEPLOYMENT.md)

---

## 既存リソース (動作中)

| リソース | 値 |
|---------|-----|
| AWS Account | 243770953619 |
| Region | ap-northeast-1 (東京) |
| AgentCore Runtime ARN | `arn:aws:bedrock-agentcore:ap-northeast-1:243770953619:runtime/hermes_hermes-JhXNFGCzcC` |
| Hermes デフォルトモデル | `global.anthropic.claude-sonnet-4-6` |
| ECR イメージ | hermes_hermes-JhXNFGCzcC (ap-northeast-1) |
| S3 ワークスペース | `hermes-agentcore-user-files-243770953619-ap-northeast-1` |
| Cognito UserPool | `ap-northeast-1_kszYxj02W` |
| KMS CMK | `arn:aws:kms:ap-northeast-1:243770953619:key/1bf72626-d881-4c7c-a4d8-b81e7105d3ac` |

---

## 動作確認 (起動直後)

```bash
cd /Users/tobisako/dev/claude_code/dev/ClaudeCodeOnHermesAgent
./hermes-chat
You> こんにちは
Hermes> （応答が返れば OK）
You> /exit
```

---

## 過去セッションのタスク履歴 (完了分)

1. ✅ AWS Bedrock 直接呼出し動作確認（boto3 + Sonnet 4.6）
2. ✅ AgentCore CLI install (`@aws/agentcore`)
3. ✅ aws-samples clone + 改造
4. ✅ Colima install (Apple Silicon Docker)
5. ✅ CDK Bootstrap (東京)
6. ✅ Phase 1 デプロイ (security/guardrails/agentcore/observability)
7. ✅ Phase 2 デプロイ (Hermes コンテナ + AgentCore Runtime)
8. ✅ モデル ID 修正 (`global.anthropic.claude-sonnet-4-6`)
9. ✅ Anthropic ユースケース申請（surf 自動化）
10. ✅ AWS Marketplace IAM 権限追加
11. ✅ AgentCore Runtime 強制再起動
12. ✅ ローカル CLI 対話確認
13. ✅ GitHub リポジトリ作成・公開（このリポ）

詳細経緯: コミットログ + Google Doc 申請記録（社内）参照

---

## トラブルシュート索引

| エラー | 対処 |
|--------|------|
| HTTP 404 use case details | [ANTHROPIC_APPLICATION.md](ANTHROPIC_APPLICATION.md) |
| HTTP 403 marketplace | execution_role 権限追加 |
| HTTP 400 invalid model id | inference profile id 確認 |
| `data: null` 応答 | CloudWatch Logs で実エラー確認 |
| `tsc: command not found` | `npm install -g typescript` |
| Docker daemon error | Colima 起動 `colima start` |

---

## 連絡

旧セッションの作業ログ:
- `/Users/tobisako/dev/claude_code/gj-board-management/ceo-office/HermesAgent/`（旧場所）
- claude-mem セッション記録（自動保存）

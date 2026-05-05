# アーキテクチャ

## Phase 1-2: ローカル CLI → AgentCore (基本構成)

```
[ ローカル PC: ./hermes-chat ]
        │ HTTPS + AWS SigV4
        ▼
[ AWS Bedrock AgentCore Runtime ]  ← Firecracker microVM (pay-per-use)
        │
   ┌────────────────────────────────────────┐
   │ Hermes コンテナ (Python 3.11)          │
   │  - anthropic SDK monkey-patch          │
   │    (Anthropic() → AnthropicBedrock())  │
   │  - MCP servers (ChatWork, Backlog)     │
   └────────────────────────────────────────┘
        │ AnthropicBedrock client (SigV4)
        ▼
[ AWS Bedrock Foundation Models ]
   global.anthropic.claude-sonnet-4-6
```

VPC なし。AgentCore は AWS マネージドのため、ユーザー VPC 不要。

---

## Phase 3: ChatWork 連携パイプライン

```
[ ChatWork ]
    │  POST (HMAC-SHA256 署名付き)
    ▼
[ API Gateway HTTP API ]
    │  /webhook/chatwork
    ▼
[ Lambda A: chatwork-webhook-handler ]
    │  1. X-ChatWorkWebhookSignature 検証
    │  2. bot 自己メッセージ除外
    │  3. SQS に enqueue
    ▼
[ SQS Queue ]
    │  非同期バッファ（API GW タイムアウト回避）
    ▼
[ Lambda B: hermes-chatwork-consumer ]
    │  1. 許可ルーム判定
    │  2. build_prompt() でプロンプト生成
    │  3. AgentCore invoke
    ▼
[ AWS Bedrock AgentCore Runtime ]
    │  Hermes コンテナ内で処理
    │    - Backlog MCP ツール呼出（検索・参照・更新）
    │    - ChatWork MCP ツール呼出（必要に応じ）
    ▼
[ ChatWork API ]  ← Hermes の返信を投稿
    │
    ▼
[ ChatWork ルーム ]  ← ユーザーが返信を受け取る
```

### ChatWork → Backlog 操作フロー（代表例）

```
ユーザー: 「期限切れのチケットを出して」
    ↓
Lambda B が AgentCore へ送信
    ↓
Hermes が Backlog MCP の search_issues ツール呼出
    ↓
Backlog API から結果取得
    ↓
Hermes が [info][title] フォーマットで整形
    ↓
ChatWork に返信投稿
    ↓
ユーザーが ChatWork で結果を受け取る
```

---

## コンポーネント一覧

| コンポーネント | 場所 | 役割 |
|------------|------|------|
| `./hermes-chat` | リポジトリルート | ローカル対話 CLI (boto3) |
| AgentCore Runtime | AWS マネージド | Hermes コンテナ実行基盤 (Firecracker microVM) |
| `infra/app/hermes/main.py` | ECR イメージ | AgentCore エントリーポイント + SDK monkey-patch |
| `infra/app/hermes/mcp_servers/chatwork_server.py` | Hermes コンテナ内 | ChatWork API を MCP ツールとして公開 |
| `infra/app/hermes/mcp_servers/backlog_server.py` | Hermes コンテナ内 | Backlog API を MCP ツールとして公開 |
| `infra/lambda/hermes_chatwork_consumer/` | Lambda (SQS trigger) | SQS 受信 → AgentCore 呼出 → ChatWork 返信 |

---

## IAM 認証フロー

```
AgentCore Runtime 起動時:
  execution_role を assume
    → 一時クレデンシャル発行
    → コンテナ内に環境変数注入
        AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN

Hermes コンテナ:
  boto3 / AnthropicBedrock SDK が env の credentials を自動取得
    → SigV4 署名付きリクエスト
    → Bedrock API へ
```

Anthropic API キー (`sk-ant-...`) は不要。IAM ロールの信頼チェーンで完結。

---

## コスト構成（東京リージョン、固定分）

| サービス | 月額 USD | 月額 JPY |
|---------|---------|---------|
| KMS CMK 1個 | $1 | ¥150 |
| Secrets Manager 5個 | $2 | ¥300 |
| CloudWatch Logs | ~$1 | ¥150 |
| ECR ストレージ (~500MB) | $0.05 | ¥8 |
| S3 (workspace) | <$0.01 | ¥1 |
| **固定合計** | **~$4/月** | **~¥600/月** |

+ Bedrock 従量課金 (pay-per-token)
- Claude Sonnet 4.6: 入力 $3/M tokens、出力 $15/M tokens

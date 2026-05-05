# ClaudeCodeOnHermesAgent

Claude Code 風の自律 AI エージェント [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research 製) を **AWS Bedrock AgentCore 上にサーバーレス構成でデプロイ**するプロジェクト。

派生元: [aws-samples/sample-host-hermesagent-on-amazon-bedrock-agentcore](https://github.com/aws-samples/sample-host-hermesagent-on-amazon-bedrock-agentcore) (MIT-0)

## できること

- **ChatWork でチャットするだけで Backlog を操作できる**
  - ChatWork のルームで Hermes に話しかける → Backlog のチケット検索・参照・更新
  - 例: 「期限切れのチケットを出して」→ Hermes が Backlog を検索して返信
- ChatWork 以外でも `./hermes-chat` からターミナルで直接対話可能
- Backlog 以外の MCP ツールも追加すれば任意の外部サービスを操作可能

## 主な特徴

- **完全 AWS 完結**: Anthropic API キー不要、Bedrock SigV4 IAM 認証
- **サーバーレス**: AWS Bedrock AgentCore (Firecracker microVM, pay-per-use)
- **超低コスト**: 月額固定 **¥300〜750** (VPC 全削除版改造)
- **Mac ターミナルから対話**: 同梱 `./hermes-chat` ローカル CLI

## なぜ Anthropic API キー不要で動くのか

### ① AWS と Anthropic の商用契約

```
[Anthropic] ─── 卸売契約 ──→ [AWS]
                              │
                              ▼ AWS Bedrock として再販
                           [AWS 顧客 = あなた]
                              │
                              ▼ AWS 請求書で支払い
```

- Anthropic は **AWS Marketplace を通じてモデルを卸売**
- AWS が Anthropic モデルをマネージドサービス化（Bedrock）
- AWS 顧客は Anthropic と直接契約 **不要**
- 課金は AWS 請求書に統合

→ Anthropic API キー（`sk-ant-...`）の代わりに **AWS の IAM 認証** で代替

### ② AWS SigV4 認証（API キー無し）

```
[Hermes コンテナ]
   │ HTTP リクエスト
   │ + AWS Signature v4 ヘッダ
   │   - AWS_ACCESS_KEY_ID
   │   - HMAC-SHA256 署名
   │   - timestamp
   │   - region
   ▼
[Bedrock API エンドポイント]
   │ 1. 署名検証
   │ 2. IAM ポリシー確認 (bedrock:InvokeModel ある?)
   │ 3. Marketplace サブスクリプション確認
   ▼
[Anthropic Claude モデル 実行]
```

API キー不要、毎リクエストに **IAM クレデンシャルで HMAC-SHA256 署名** することで認証完了。

### ③ AgentCore Runtime の自動 IAM 委譲

Hermes コンテナにユーザーが認証情報を渡さない。代わりに AWS が自動委譲する仕組み:

```
[AgentCore Runtime] (AWS マネージド)
   │ コンテナ起動時
   │ → execution_role を assume
   │ → 一時クレデンシャル発行（AWS_ACCESS_KEY_ID + AWS_SECRET + AWS_SESSION_TOKEN）
   │ → コンテナ内に環境変数として注入
   ▼
[Hermes コンテナ]
   │ boto3 / AnthropicBedrock SDK が
   │ 環境変数の credentials を自動取得
   │ → SigV4 署名付きリクエスト
   ▼
[Bedrock]
```

→ **Hermes コンテナは何も意識せず、AWS の信頼チェーンで認証完了**

CloudWatch ログで実際に確認できる:
```
INFO Found credentials from IAM Role: execution_role
```

これが「IAM ロールから一時クレデンシャル取得した」という意味。

### ④ monkey-patch で Anthropic SDK を Bedrock 経由に置換

Hermes 本体は元々 Anthropic API キーで動く設計。本プロジェクトでは `app/hermes/main.py` で:

```python
import anthropic
_OrigAnthropic = anthropic.Anthropic

class _PatchedAnthropic:
    def __new__(cls, *args, **kwargs):
        # API キー無しで呼ばれたら Bedrock に置換
        return anthropic.AnthropicBedrock(aws_region=region)

anthropic.Anthropic = _PatchedAnthropic  # SDK ごと差し替え
```

→ Hermes コードは「Anthropic 直叩き」のつもりで動くが、実際は **AnthropicBedrock 経由**で AWS Bedrock へ流れる

`AnthropicBedrock` クライアントが自動で SigV4 署名する → API キー不要

### まとめ表

| 質問 | 答え |
|------|------|
| 課金は誰? | **AWS 請求書**（Bedrock 利用料として統合） |
| Anthropic 直契約は必要? | **不要**（AWS Marketplace 経由） |
| Anthropic API キー (`sk-ant-...`) は? | **不要**（IAM SigV4 認証で代替） |
| Hermes コンテナは何の認証情報を持つ? | AgentCore が自動注入する **一時 IAM クレデンシャル** |
| Anthropic は中身を見る? | リクエスト本文は見ない（AWS 経由で隠蔽）。学習にも使われない契約 |
| Claude Code (claude.com) アカウントは関係? | **無関係** |

## オリジナル aws-samples からの主な改造

### 1. VPC 全削除（コスト最適化）

aws-samples のオリジナルは月額 約 **¥25,500** (NAT Gateway $44 + Interface VPC Endpoint 6種 $120/月)。

本プロジェクトでは VPC を **完全削除** して月額 **¥300〜750** に圧縮した。

- 削除: VPC / NAT Gateway / Interface VPC Endpoint × 6 / Security Group
- Lambda は VPC 外で動作（外部 webhook 受信 OK、AWS API 直接アクセス）
- AgentCore Runtime は AWS マネージドのため、ユーザー VPC 不要

詳細: [docs/VPC_REMOVAL.md](docs/VPC_REMOVAL.md)

### 2. Claude Sonnet 4.6 デフォルト使用 + モデル ID 修正

- aws-samples の `app/hermes/main.py` のデフォルト モデル ID `us.anthropic.claude-sonnet-4-6` (v1 抜け) は Bedrock 拒否される
- 修正: `global.anthropic.claude-sonnet-4-6` (cross-region inference profile, v1 サフィックスなし)

### 3. AWS Marketplace IAM 権限追加

Bedrock の Anthropic モデル呼出時、内部で Marketplace サブスクリプション検証が走る。execution_role に下記権限を追加:

- `aws-marketplace:ViewSubscriptions`
- `aws-marketplace:Subscribe`
- `aws-marketplace:Unsubscribe`
- `aws-marketplace:GetSubscriptionInformation`
- 等

### 4. ローカル CLI 同梱: `./hermes-chat`

Python boto3 ベースの対話型クライアント。

```bash
./hermes-chat
You> こんにちは
Hermes> こんにちは！何かお手伝いできることはありますか？
You> /new      # 新セッション
You> /exit
```

## ⚠️ Anthropic ユースケース申請が必要

**重要**: Bedrock 上の Anthropic Claude モデルを実際に呼び出すには、AWS Console から **Anthropic ユースケース詳細フォーム**の提出が必須。

申請しないと以下のエラー:

```
HTTP 404: Model use case details have not been submitted for this account.
Fill out the Anthropic use case details form before using the model.
```

申請手順・記入例・トラブルシュート: [docs/ANTHROPIC_APPLICATION.md](docs/ANTHROPIC_APPLICATION.md)

## デプロイ

### 前提

| ツール | バージョン |
|--------|-----------|
| AWS CLI v2 | 2.34+ |
| AWS CDK | 2.150+ |
| Node.js | >=18 |
| Python | >=3.10 |
| Docker | Apple Silicon native (Colima 推奨、Docker Desktop 不要) |
| AgentCore CLI | `npm i -g @aws/agentcore` |
| TypeScript | (`agentcore/cdk` 内で自動 install) |

### 手順

```bash
# 0. .env 作成
cp .env.example .env
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY 等を記入

# 1. CDK Bootstrap (1度だけ、東京)
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-1

# 2. Phase 1: Foundation
./scripts/deploy.sh phase1
# → security / guardrails (空) / agentcore / observability

# 3. Phase 2: Hermes コンテナ build + AgentCore Runtime
./scripts/deploy.sh phase2
# → ECR push + AgentCore Runtime 登録（10-15分）

# 4. Anthropic ユースケース申請（AWS Console から手動）
# → docs/ANTHROPIC_APPLICATION.md 参照

# 5. 動作確認
cd ..
./hermes-chat
You> Hello
```

詳細: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## アーキテクチャ

```
[ ローカル PC: ./hermes-chat ]
        │ HTTPS (boto3, SigV4)
        ▼
[ AWS Bedrock AgentCore Runtime ]  ← Firecracker microVM (pay-per-use)
        │
   Hermes コンテナ (Python 3.11, Bedrock monkey-patch)
        │
   AnthropicBedrock client (SigV4 IAM auth)
        │
        ▼
[ Bedrock Foundation Models ]
   global.anthropic.claude-sonnet-4-6
```

VPC なし。Lambda Router + ChatWork 連携は Phase 3 オプション拡張。

## 月額コスト（東京、固定分）

| サービス | 月額 USD | 月額 JPY (¥150/$) |
|---------|---------|-----------|
| KMS CMK 1個 | $1 | ¥150 |
| Secrets Manager 5個 | $2 | ¥300 |
| CloudWatch Logs | ~$1 | ¥150 |
| ECR ストレージ (~500MB) | $0.05 | ¥8 |
| S3 (workspace) | <$0.01 | ¥1 |
| **小計** | **~$4/月** | **~¥600/月** |

+ Bedrock 利用料 (pay-per-token)
- Claude Sonnet 4.6: 入力 $3/M, 出力 $15/M tokens

## ライセンス

派生元 aws-samples のライセンス継承: **MIT-0** (MIT No Attribution)

派生元: https://github.com/aws-samples/sample-host-hermesagent-on-amazon-bedrock-agentcore

Hermes Agent 本体: https://github.com/NousResearch/hermes-agent (Apache-2.0)

## 関連ドキュメント

- [docs/ANTHROPIC_APPLICATION.md](docs/ANTHROPIC_APPLICATION.md) — Anthropic 申請の経緯と手順
- [docs/VPC_REMOVAL.md](docs/VPC_REMOVAL.md) — VPC 削除改造の詳細
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — デプロイ詳細手順
- [docs/COST_ESTIMATE.md](docs/COST_ESTIMATE.md) — 月額コスト試算

## 謝辞

- [Nous Research](https://nousresearch.com/) — Hermes Agent 本体
- [AWS Samples](https://github.com/aws-samples) — Bedrock AgentCore 統合サンプル
- [@garrytan](https://github.com/garrytan) / G-Stack — Claude Code 開発フロー

---

🤖 構築・ドキュメンテーション: Claude Opus 4.7 (1M context) via [Claude Code](https://claude.com/claude-code)

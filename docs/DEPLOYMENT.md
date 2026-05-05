# デプロイ手順

## 0. 事前準備

### 必要ツール

```bash
# AWS CLI v2 (最新)
brew install awscli

# Node.js 18+
brew install node  # or nvm

# Python 3.10+
# (System Python 3.13/3.14 でも OK)

# Docker (Apple Silicon)
brew install colima docker docker-compose docker-buildx
colima start --arch aarch64 --cpu 4 --memory 8 --disk 60

# AWS CDK
npm install -g aws-cdk

# AgentCore CLI
npm install -g @aws/agentcore

# TypeScript (グローバルでも可、agentcore/cdk 内 install でも可)
npm install -g typescript
```

### Docker 注意点

- **Apple Silicon Mac**: Docker Desktop 旧版 (x86_64) は動かない。Colima 推奨
- Docker Desktop の商用ライセンス回避にも Colima が良い
- 必要時のみ `colima start`、終わったら `colima stop`（メモリ解放）

### AWS 認証

専用 IAM ユーザーを作成して使う:

- 用途: `hermes-deploy-bot` 等
- 必要権限（最小ポリシー、本リポ後日 IAM JSON 追加予定）:
  - CloudFormation 全権
  - S3 / IAM / ECR / Lambda / Bedrock / bedrock-agentcore
  - aws-marketplace:*
  - MFA 必須

Access Key を `.env` に保管（git 追跡対象外）。

## 1. .env 作成

```bash
cp .env.example .env
# エディタで AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY 等を記入
```

## 2. CDK Bootstrap (初回のみ)

```bash
cd sample-aws
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 東京リージョンで Bootstrap
cdk bootstrap aws://<ACCOUNT_ID>/ap-northeast-1
```

## 3. Phase 1: Foundation (KMS / Secrets / IAM / S3)

```bash
./scripts/deploy.sh phase1
```

作成されるスタック:
- `hermes-agentcore-security` (KMS / Secrets Manager / Cognito)
- `hermes-agentcore-guardrails` (空、disabled)
- `hermes-agentcore-agentcore` (IAM execution_role / S3 bucket)
- `hermes-agentcore-observability` (CloudWatch dashboard)

時間: 1〜2 分

## 4. Phase 2: Hermes コンテナビルド + AgentCore Runtime

```bash
./scripts/deploy.sh phase2
```

内部で:
1. `agentcore/aws-targets.json` 自動生成（リージョン情報）
2. `~/hermes-agent` に Hermes 本体 clone（無ければ）
3. `app/hermes/hermes-agent/`, `app/hermes/bridge/` に同期
4. AWS CodeBuild で Docker イメージビルド
5. ECR に push
6. AgentCore Runtime 登録（`agentcore deploy`）
7. `cdk.json` に Runtime ARN 書き戻し

時間: 10〜15 分（初回）、5 分前後（差分更新）

### よくあるエラー

#### `tsc: command not found`

```bash
npm install -g typescript
```

または `agentcore/cdk` 内で:
```bash
cd agentcore/cdk && npm install
```

#### `error TS5107: Option 'moduleResolution=node10' is deprecated`

`agentcore/cdk/tsconfig.json` の `ignoreDeprecations` を削除（TS 5.x で `"6.0"` は無効）。本リポではすでに修正済み。

#### `aws-targets.json: region=us-west-2 (期待: ap-northeast-1)`

`deploy.sh` の `_REGION=$(aws configure get region 2>/dev/null || echo "us-west-2")` が `~/.aws/config` を見る:

```bash
aws configure set region ap-northeast-1
```

## 5. Anthropic ユースケース申請

[docs/ANTHROPIC_APPLICATION.md](ANTHROPIC_APPLICATION.md) 参照。

申請しないと HTTP 404 が返る。

## 6. 動作確認

### AgentCore CLI 経由

```bash
cd sample-aws
agentcore invoke "Say hi briefly" --runtime hermes
```

成功例:
```
Hi there! 👋 How can I help you today?
```

### 同梱の対話 CLI 経由

```bash
cd ..
./hermes-chat
You> こんにちは
Hermes> こんにちは！何かお手伝いできることはありますか？
You> /new          # 新規セッション
You> /id           # 現在の session-id 表示
You> /exit
```

## 7. Phase 3 (オプション): Router Lambda + API Gateway

外部 webhook (Telegram / Slack / Discord / Feishu / **ChatWork** など) との連携が必要な場合:

```bash
cd sample-aws
./scripts/deploy.sh phase3
```

公開 API URL: `https://xxx.execute-api.ap-northeast-1.amazonaws.com`

ChatWork 用は `lambda/router/index.py` に `_handle_chatwork()` を追加（PR 歓迎）。

## 8. 撤退（コスト停止）

```bash
cd sample-aws
./scripts/teardown.sh
```

CloudFormation スタック全削除 + ECR イメージ削除。

S3 ワークスペースバケットは `RemovalPolicy.RETAIN` で残るので、不要なら手動削除。

## 動作環境（検証済み）

- macOS 26.4 (Apple Silicon, M-series)
- AWS CLI 2.34.42
- AWS CDK 2.1120.0
- Node.js 24.14.0
- Python 3.14.4
- Docker Engine 29.4.2 (via Colima)
- 東京リージョン (`ap-northeast-1`)
- Bedrock model: `global.anthropic.claude-sonnet-4-6`

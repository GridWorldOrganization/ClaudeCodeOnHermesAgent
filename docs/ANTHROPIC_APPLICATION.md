# Anthropic ユースケース申請（必須）

## 概要

AWS Bedrock 上で Anthropic Claude モデルを実際に呼び出すには、**Anthropic ユースケース詳細フォーム**の提出がアカウント単位で必須となる。

申請しない場合、以下の HTTP 404 エラーで全リクエスト失敗する:

```
HTTP 404: Model use case details have not been submitted for this account.
Fill out the Anthropic use case details form before using the model.
If you have already filled out the form, try again in 15 minutes.
```

## なぜ必要なのか

- AWS Bedrock の旧「Model access」ページ（個別モデル access リクエスト）は廃止された
- Anthropic モデルだけは初回利用前に**ユースケース詳細**を Anthropic と共有する必要がある
- 申請内容は Anthropic に渡され、安全性・適法性審査の参考になる
- AWS Console の表示:

  > "Anthropic requires first-time customers to submit use case details before invoking a model, once per account or once at the organization's management account. The information you submit will be shared with Anthropic."

## 申請手順

### 1. Bedrock Console を開く

```
https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/model-catalog
```

リージョン `us-east-1` (バージニア北部) で開くのが確実。

### 2. 黄色いアラート内の「Submit use case details」ボタンをクリック

モデルカタログのページ上部に Anthropic 申請の案内バーが表示される。

### 3. フォームに記入

| フィールド | 内容 | 例 |
|-----------|------|-----|
| Company name | 会社名 | `Your Company Name` |
| Company website URL | 会社 Web サイト URL | `https://your-company.example.com` |
| Industry | 業界（ドロップダウン）| `Software as a Service` |
| Intended users | 利用対象（チェックボックス）| `Internal users (employees, staff, team members)` |
| Use case description | 用途説明（英語、最大 500 文字） | (下記参照) |

#### Use case description 例（英語）

```
Internal AI assistant for our small business team. We integrate Claude
via Bedrock AgentCore with ChatWork (Japanese business messaging) for
information lookup, task automation, and customer inquiry drafting.
All outputs are reviewed by authenticated employees before any external
delivery. We do not redistribute model outputs to end users. Estimated
volume: 50-100 requests per day.
```

### 4. Submit

「Submit use case details」ボタン押下 → 緑バー通知:

```
Use case details for Anthropic submitted successfully.
```

→ ダイアログ自動クローズ

### 5. 反映待ち

- 即時 〜 15分程度
- AWS のメッセージ: "If you recently fixed this issue, try again after 2 minutes."

## 申請後に発生しうる別のエラー

### エラー 1: HTTP 403 Marketplace 権限不足

```
HTTP 403: Model access is denied due to IAM user or service role is
not authorized to perform the required AWS Marketplace actions
(aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)
to enable access to this model.
```

#### 原因

AgentCore の execution_role に Marketplace アクションの権限が含まれていない。

#### 対処（IAM ポリシー追加）

`stacks/agentcore_stack.py` の execution_role に以下を追加:

```python
self.execution_role.add_to_policy(
    iam.PolicyStatement(
        sid="MarketplaceAccess",
        actions=[
            "aws-marketplace:ViewSubscriptions",
            "aws-marketplace:Subscribe",
            "aws-marketplace:Unsubscribe",
            "aws-marketplace:GetSubscriptionInformation",
            "aws-marketplace:DescribeAgreement",
            "aws-marketplace:GetAgreementApprovalRequest",
            "aws-marketplace:ListEntitlements",
        ],
        resources=["*"],
    )
)
```

`cdk deploy hermes-agentcore-agentcore` で更新。

#### 反映後も 403 が続く場合

AgentCore Runtime コンテナがクレデンシャルをキャッシュしている可能性。**強制再デプロイ**:

```bash
agentcore deploy --yes --verbose
```

10〜15 分でコンテナ再ビルド + Runtime 更新。

### エラー 2: HTTP 400 Invalid model identifier

```
HTTP 400: The provided model identifier is invalid.
```

モデル ID の命名規則に注意:

| モデル | 正しい inference profile id |
|--------|----------------------------|
| Claude Sonnet 4.6 | `global.anthropic.claude-sonnet-4-6` (v1 サフィックス**なし**) |
| Claude Opus 4.6 | `global.anthropic.claude-opus-4-6-v1` (v1 サフィックス**あり**) |
| Claude Sonnet 4.5 | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Claude Haiku 4.5 | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |

注意: モデルごとに -v1 や -v1:0 の有無が違う。`aws bedrock list-inference-profiles --region us-east-1` で正確な ID を取得すべき。

## 動作確認

### ローカルから Bedrock を直接叩く（IAM 動作確認）

```bash
cat > /tmp/body.json <<'EOF'
{"anthropic_version":"bedrock-2023-05-31","max_tokens":30,"messages":[{"role":"user","content":"Hi"}]}
EOF

aws bedrock-runtime invoke-model \
  --model-id global.anthropic.claude-sonnet-4-6 \
  --body fileb:///tmp/body.json \
  --content-type application/json \
  --region us-east-1 \
  /tmp/out.json

cat /tmp/out.json
```

成功すれば `Hi there!` 等の応答が返る。

### AgentCore 経由（Hermes）

```bash
cd infra
agentcore invoke "Say hi briefly" --runtime hermes
```

または同梱の対話 CLI:

```bash
./hermes-chat
You> こんにちは
```

## トラブルシュート 切り分け

| 状況 | 原因 |
|------|------|
| 申請ボタンが見つからない | 「Model access」ページは廃止、Model **Catalog** ページの黄色バー |
| Submit 後すぐ 404 が続く | 反映に最大 15 分。少し待つ |
| ローカル直接呼出は OK だが AgentCore 経由 403 | execution_role の Marketplace 権限不足 |
| IAM ポリシー追加後も 403 | コンテナ クレデンシャルキャッシュ → `agentcore deploy` で強制再起動 |
| HTTP 400 invalid model id | `bedrock list-inference-profiles` で正確な ID 確認 |

## 参考資料

- [AWS Bedrock Model Access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [Bedrock Inference Profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- [Anthropic Use Case Submission FAQ (AWS)](https://docs.aws.amazon.com/bedrock/latest/userguide/anthropic.html)

## 実体験記録

本プロジェクトでは以下の経緯を辿った（2026-05-05 実施）:

1. AgentCore Runtime デプロイ完了 → 最初の `agentcore invoke "Say hi"` は猶予期間内で成功
2. 数分後の継続テストで HTTP 404「ユースケース申請必要」エラー発生
3. AWS Console で Submit use case details フォーム記入・提出
4. 反映後、HTTP 403「Marketplace 権限不足」エラーに変化
5. execution_role に Marketplace アクション追加 + `cdk deploy hermes-agentcore-agentcore`
6. それでも 403 続く → `agentcore deploy --yes` でコンテナ強制再ビルド
7. 動作確認成功（応答正常）

期間: 約 1 時間半（うちコンテナ再ビルド時間が大半）

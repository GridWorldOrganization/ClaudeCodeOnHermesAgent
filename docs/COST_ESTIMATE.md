# 月額コスト試算

## 固定費（VPC 削除版、東京リージョン）

| サービス | 月額 USD | 月額 JPY (¥150/$) |
|---------|---------|-----------|
| Lambda (100万リク無料枠) | $0 | ¥0 |
| API Gateway (100万リク無料枠 12ヶ月) | $0〜1 | ¥0〜150 |
| AgentCore Runtime (pay-per-use) | $1〜5 | ¥150〜750 |
| S3 (workspace, 数MB) | $0.01 | ¥1.5 |
| Secrets Manager 5個 | $2 | ¥300 |
| KMS CMK 1個 | $1 | ¥150 |
| CloudWatch Logs (1GB 無料) | $0〜1 | ¥0〜150 |
| ECR storage (~500MB) | $0.05 | ¥8 |
| **小計** | **~$4〜10/月** | **~¥600〜1,500/月** |

## Bedrock 利用料（pay-per-token）

### Claude Sonnet 4.6 (default)

- 入力: $3 / 100万 tokens
- 出力: $15 / 100万 tokens

#### 利用想定

| 利用ボリューム | 月額 USD | 月額 JPY |
|--------------|---------|---------|
| 個人 (10 会話/日 × 平均1k入力 + 500出力) | $0.40 | ¥60 |
| 小規模ビジネス (100 会話/日 × 5k入力 + 2k出力) | $19 | ¥2,850 |
| ChatWork 1ルーム / アクティブ業務 (500 会話/日 × 平均10k+3k) | $90 | ¥13,500 |

### Claude Opus 4.6 (高性能、要時のみ)

- 入力: $15 / 100万 tokens
- 出力: $75 / 100万 tokens

Sonnet 4.6 比 5倍コスト。複雑なコード生成・推論が必要な時のみ。

## オリジナル aws-samples vs 改造版

| 構成 | 固定月額 | 用途想定 |
|------|---------|---------|
| **オリジナル aws-samples** | $170 (¥25,500) | エンタープライズ、監査要件あり |
| **本リポ (VPC 削除版)** | **$4〜10 (¥600〜1,500)** | PoC / 個人 / 小規模ビジネス |

削減率: **96%**

## ピンポイント節約 Tips

### 1. 不使用時は Runtime を削除

AgentCore Runtime 自体は固定費ほぼゼロ（pay-per-use）。ただし安心のため:
```bash
./scripts/teardown.sh
```

S3 / ECR / IAM などは残る（数百円/月）。

### 2. CloudWatch Logs の保持期間短縮

```python
log_retention=logs.RetentionDays.ONE_WEEK  # 既存は ONE_MONTH
```

### 3. Sonnet を Haiku 4.5 に切替（簡易タスク用）

`app/hermes/main.py`:
```python
model = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
```

Haiku 4.5: 入力 $0.80 / 100万 tokens、出力 $4 / 100万 tokens

Sonnet 4.6 比で 4倍 安い。

### 4. KMS CMK を AWS managed key に切替

`stacks/security_stack.py` で `kms.Key` 削除し、各リソースで `BucketEncryption.S3_MANAGED` 等使えば $1/月 削減可能。

### 5. ECR ライフサイクルポリシー

古いイメージ自動削除でストレージコスト最小化。

```python
ecr_repo.add_lifecycle_rule(
    max_image_count=3,
    description="Keep only the latest 3 images",
)
```

## 撤退コスト

`./scripts/teardown.sh` 実行で CloudFormation スタック全削除。

**S3 / ECR は手動削除が必要**:

```bash
# S3 バケット
aws s3 rb s3://hermes-agentcore-user-files-<ACCOUNT>-ap-northeast-1 --force

# ECR リポジトリ
aws ecr delete-repository --repository-name <name> --force
```

完全撤退後、月額 0 円（初期 cdk-bootstrap S3 / ECR 数円 残る程度）。

## 実測値（2026-05-05 時点、PoC 段階）

- 1日: AgentCore 利用 30分 / Bedrock 50リクエスト / 平均 800 tokens
- 推定月額: $0.50 + 固定費 $4 = **$4.50/月 (~¥675)**

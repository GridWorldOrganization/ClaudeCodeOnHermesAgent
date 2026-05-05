# VPC 削除によるコスト最適化

## オリジナル aws-samples 構成のコスト

| リソース | 数量 × 単価 | 月額 USD |
|---------|------------|---------|
| **NAT Gateway** | 1個 × $0.062/h × 720h | $44.64 |
| **Interface VPC Endpoint** (Bedrock/SecretsMgr/STS/ECR/ECR-Docker/CW Logs) | 6種 × 2 AZ × 720h × $0.014 | $120.96 |
| KMS CMK | 1個 | $1 |
| Secrets Manager | 4-5個 | $2 |
| **合計（固定）** |  | **~$170/月 (¥25,500)** |

## 改造後（VPC 全削除）のコスト

| リソース | 月額 USD | 月額 JPY |
|---------|---------|---------|
| Lambda | 無料枠 | ¥0 |
| API Gateway | 無料枠 | ¥0 |
| AgentCore Runtime | $1〜5 (pay-per-use) | ¥150〜750 |
| S3 (workspace) | $0.01 | ¥1.5 |
| Secrets Manager 5個 | $2 | ¥300 |
| KMS CMK 1個 | $1 | ¥150 |
| CloudWatch Logs | 無料枠 | ¥0 |
| **合計** | **~$3〜7/月** | **¥450〜1,050/月** |

**コスト削減: 月額 ¥25,500 → ¥1,050 (約 96% 削減)**

## なぜ VPC を削除できるのか

aws-samples のオリジナル設計思想:

- 商用エンタープライズ想定
- Lambda が VPC 内 = 外部通信を VPC Flow Logs で完全捕捉
- Bedrock/SecretsMgr/STS を Interface Endpoint 経由 = 外部ネット非経由
- 監査・コンプライアンス対応

しかし **個人 PoC・小規模利用では完全に過剰**:

- AgentCore Runtime は **AWS マネージド**（Firecracker microVM）
- マネージドサービスにユーザー VPC 紐付け不要
- Lambda は **VPC 外で動作可能** (`router_stack.py` / `cron_stack.py` 確認済み、VPC 設定なし)
- AWS API 通信は標準パブリックエンドポイントで充分
- ChatWork 等の外部 webhook 受信は API Gateway パブリックエンドポイント

## 改造内容（最小差分）

### 1. `app.py` から vpc_stack 削除

```python
# 削除
from stacks.vpc_stack import HermesVpcStack
vpc_stack = HermesVpcStack(app, f"{project}-vpc")

# agentcore_stack の vpc 引数も削除
agentcore_stack = HermesAgentCoreStack(
    app,
    f"{project}-agentcore",
    kms_key_arn=security_stack.kms_key.key_arn,  # vpc=vpc_stack.vpc を削除
)
```

### 2. `stacks/agentcore_stack.py` から VPC 関連削除

```python
# 削除した import
# from aws_cdk import aws_ec2 as ec2

# 削除した引数
# def __init__(..., vpc: ec2.IVpc, ...)

# 削除した Security Group
# self.sg = ec2.SecurityGroup(self, "AgentCoreSG", vpc=vpc, ...)
```

### 3. `scripts/deploy.sh` から vpc stack 削除

```bash
$CDK deploy \
    "${PROJECT_NAME}-security" \
    "${PROJECT_NAME}-guardrails" \
    "${PROJECT_NAME}-agentcore" \
    "${PROJECT_NAME}-observability" \
    --require-approval never
# ↑ "${PROJECT_NAME}-vpc" 削除
```

### 4. `app.py` から gateway_stack（Phase 4）削除

ChatWork のような webhook 連携は API Gateway + Router Lambda（Phase 3）で対応可能。
WeChat / Feishu の WebSocket 接続が必要な場合のみ ECS Fargate ベースの gateway_stack 復活させる。

## トレードオフ

### 失うもの

- 監査要件: VPC Flow Logs での完全通信ログ捕捉
- コンプライアンス: 「データが Public Internet を経由しない」保証
- セキュリティ層: VPC レベルでのネットワーク分離

### 得るもの

- 月額コスト 96% 削減
- デプロイ時間短縮（VPC 関連 5+ リソース削減）
- 構成シンプル化（CloudFormation スタック数削減）

## どんな場合に VPC を残すべきか

- 金融・医療・政府系など**規制業界**
- HIPAA / PCI-DSS / SOC 2 等の**監査要件あり**
- マルチテナントで他社データを扱う
- 特定の IP レンジからのみアクセス許可したい

PoC・社内利用・低スループット用途なら、本改造（VPC 削除）で十分。

#!/usr/bin/env bash
# Workspace init + secret loading + MCP config + hermes-agent contract server start.
set -e

WORKSPACE="${HERMES_HOME:-/mnt/workspace/.hermes}"

# Create workspace directories if they don't exist.
for dir in memories skills sessions logs cache cron; do
    mkdir -p "$WORKSPACE/$dir" 2>/dev/null || true
done

# Copy bundled SOUL.md if user doesn't have one yet.
if [ ! -f "$WORKSPACE/SOUL.md" ] && [ -f /app/hermes-agent/SOUL.md ]; then
    cp /app/hermes-agent/SOUL.md "$WORKSPACE/SOUL.md"
fi

# --------------------------------------------------------------------------
# Secrets Manager → env var injection (best-effort, non-blocking).
# --------------------------------------------------------------------------
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-1}}"

_load_secret() {
    local secret_name="$1"
    local env_var="$2"
    if [ -n "${!env_var:-}" ]; then
        return 0  # Already set (e.g., for local dev).
    fi
    local value
    value=$(python3 -c "
import os, sys, boto3
from botocore.exceptions import ClientError
try:
    c = boto3.client('secretsmanager', region_name='${REGION}')
    print(c.get_secret_value(SecretId='${secret_name}')['SecretString'], end='')
except (ClientError, Exception) as e:
    sys.stderr.write(f'[entrypoint] secret load skip: {e}\n')
    sys.exit(0)
" 2>/dev/null || true)
    if [ -n "$value" ]; then
        export "$env_var=$value"
        echo "[entrypoint] $env_var loaded from $secret_name"
    fi
}

_load_secret "hermes/chatwork-api-token" "CHATWORK_API_TOKEN"
_load_secret "hermes/backlog-api-key" "BACKLOG_API_KEY"

# Backlog non-secret defaults (overridable by env).
# Set these to your own Backlog domain and project key.
export BACKLOG_DOMAIN="${BACKLOG_DOMAIN:-}"
export BACKLOG_DEFAULT_PROJECT_KEY="${BACKLOG_DEFAULT_PROJECT_KEY:-}"

# --------------------------------------------------------------------------
# Generate Hermes config.yaml with MCP servers.
# Always overwrite (idempotent — config is derived from container state).
# --------------------------------------------------------------------------
cat > "$WORKSPACE/config.yaml" <<YAMLEOF
mcp_servers:
  chatwork:
    command: python
    args:
      - /app/mcp_servers/chatwork_server.py
    enabled: true
    timeout: 30
  backlog:
    command: python
    args:
      - /app/mcp_servers/backlog_server.py
    enabled: true
    timeout: 30
  image:
    command: python
    args:
      - /app/mcp_servers/image_server.py
    enabled: true
    timeout: 120
YAMLEOF

echo "[entrypoint] $WORKSPACE/config.yaml generated (mcp_servers: chatwork, backlog, image)"

exec "$@"

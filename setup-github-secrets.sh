#!/bin/bash
# GitHub Actions Secrets 配置脚本（勿在仓库中硬编码 token）

REPO="${GITHUB_REPO:-roycehwa/book-weaver}"
TOKEN="${GITHUB_TOKEN:?Set GITHUB_TOKEN before running this script}"
KEY_ID="${GITHUB_ACTIONS_KEY_ID:?Set GITHUB_ACTIONS_KEY_ID}"
KEY="${GITHUB_ACTIONS_PUBLIC_KEY:?Set GITHUB_ACTIONS_PUBLIC_KEY}"

echo "=== 配置 GitHub Actions Secrets ==="
echo ""

encrypt_secret() {
    local secret="$1"
    python3 << PYTHON
import base64
import nacl.public

public_key = nacl.public.PublicKey(base64.b64decode("$KEY"))
sealed_box = nacl.public.SealedBox(public_key)
encrypted = sealed_box.encrypt("$secret".encode("utf-8"))
print(base64.b64encode(encrypted).decode("utf-8"))
PYTHON
}

configure_secret() {
    local name="$1"
    local value="$2"
    echo "配置 $name..."
    encrypted_value=$(encrypt_secret "$value")
    curl -s -X PUT \
        -H "Authorization: token $TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -H "Content-Type: application/json" \
        "https://api.github.com/repos/$REPO/actions/secrets/$name" \
        -d "{\"encrypted_value\":\"$encrypted_value\",\"key_id\":\"$KEY_ID\"}"
}

read -p "服务器用户名 (默认: ubuntu): " SERVER_USER
SERVER_USER=${SERVER_USER:-ubuntu}
read -sp "服务器密码: " SERVER_PASSWORD
echo ""
read -p "服务器地址: " SERVER_HOST

configure_secret "SERVER_HOST" "$SERVER_HOST"
configure_secret "SERVER_USER" "$SERVER_USER"
configure_secret "SERVER_PASSWORD" "$SERVER_PASSWORD"

echo "完成。验证: https://github.com/$REPO/settings/secrets/actions"

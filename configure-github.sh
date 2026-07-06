#!/bin/bash
# GitHub Token 配置脚本

echo "=== BookMate GitHub 配置脚本 ==="
echo ""
echo "请访问 https://github.com/settings/tokens/new 生成 Personal Access Token"
echo "需要勾选 'repo' 权限"
echo ""
read -sp "请输入 GitHub Personal Access Token: " TOKEN
echo ""

if [ -z "$TOKEN" ]; then
    echo "错误: Token 不能为空"
    exit 1
fi

cd /root/.openclaw/workspace/bookmate

# 配置凭证存储
git config credential.helper store

# 更新 remote URL 使用 Token
git remote set-url origin "https://${TOKEN}@github.com/roycehwa/bookmate.git"

echo ""
echo "正在推送代码..."
git push origin main

if [ $? -eq 0 ]; then
    echo "✅ 推送成功！"
    
    # 配置 GitHub Actions Secrets（可选）
    echo ""
    echo "是否配置 GitHub Actions Secrets？(y/n)"
    read -n 1 -r
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "配置 GitHub Actions Secrets..."
        # 这里可以添加 gh CLI 命令配置 secrets
        echo "请手动访问 https://github.com/roycehwa/bookmate/settings/secrets/actions"
        echo "添加以下 Secrets:"
        echo "  - SERVER_HOST: 101.43.19.135"
        echo "  - SERVER_USER: ubuntu"
        echo "  - SERVER_PASSWORD: <你的服务器密码>"
    fi
else
    echo "❌ 推送失败，请检查 Token 是否正确"
fi

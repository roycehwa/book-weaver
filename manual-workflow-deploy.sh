#!/bin/bash
# GitHub Actions Workflow 部署脚本

echo "=== 由于 Token 缺少 workflow 权限，请手动上传 workflow 文件 ==="
echo ""
echo "步骤 1: 访问 GitHub 仓库"
echo "  https://github.com/roycehwa/book-weaver/tree/main/.github/workflows"
echo ""
echo "步骤 2: 点击 'Add file' → 'Create new file'"
echo ""
echo "步骤 3: 文件路径输入: .github/workflows/deploy.yml"
echo ""
echo "步骤 4: 粘贴以下内容:"
echo "=========================================="
cat /root/.openclaw/workspace/bookmate/.github/workflows/deploy.yml
echo ""
echo "=========================================="
echo ""
echo "步骤 5: 点击 'Commit new file'"
echo ""
echo "或者，生成一个新的 Token（带有 workflow 权限）:"
echo "  1. 访问 https://github.com/settings/tokens/new"
echo "  2. 勾选 'repo' 和 'workflow' 权限"
echo "  3. 使用新 Token 推送"

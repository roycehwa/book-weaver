#!/bin/bash
# BookMate 一键部署脚本
# 使用: ./deploy.sh

set -e

echo "🚀 BookMate 部署脚本"
echo "===================="

# 配置
SERVER_IP="101.43.19.135"
SERVER_USER="ubuntu"
REMOTE_DIR="/var/www/html"
TEMP_DIR="/tmp/bookmate-deploy"
SSH_KEY="$HOME/.ssh/bookmate_deploy"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"

echo "📦 Step 1: 构建前端..."
cd frontend
npm ci
npm run build
cd ..

echo "🧹 Step 2: 清理临时目录..."
ssh ${SSH_OPTS} ${SERVER_USER}@${SERVER_IP} "rm -rf ${TEMP_DIR} && mkdir -p ${TEMP_DIR}"

echo "📤 Step 3: 上传新版本到临时目录..."
scp ${SSH_OPTS} -r frontend/dist/* ${SERVER_USER}@${SERVER_IP}:${TEMP_DIR}/

echo "🚀 Step 4: 部署到生产目录..."
ssh ${SSH_OPTS} ${SERVER_USER}@${SERVER_IP} "sudo rm -rf ${REMOTE_DIR}/* && sudo cp -r ${TEMP_DIR}/* ${REMOTE_DIR}/ && sudo chown -R www-data:www-data ${REMOTE_DIR}"

echo "🧹 Step 5: 清理临时目录..."
ssh ${SSH_OPTS} ${SERVER_USER}@${SERVER_IP} "rm -rf ${TEMP_DIR}"

echo "✅ 部署完成！"
echo "🔗 http://${SERVER_IP}"

#!/bin/bash
# BookMate 一键部署脚本
# 在服务器上直接运行此脚本进行部署
# 使用: ./deploy-local.sh

set -e

echo "🚀 BookMate 本地部署脚本"
echo "=========================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置
PROJECT_DIR="/opt/bookmate"
NGINX_ROOT="/var/www/html"
BACKEND_PORT="8000"

# 检查是否在正确目录
if [ ! -f "backend/main.py" ]; then
    echo -e "${RED}❌ 错误: 请在 bookmate 项目根目录运行此脚本${NC}"
    exit 1
fi

echo -e "${YELLOW}📋 部署步骤:${NC}"
echo "1. 更新代码 (git pull)"
echo "2. 更新后端依赖"
echo "3. 构建前端"
echo "4. 部署到 Nginx"
echo "5. 重启后端服务"
echo ""

# 步骤 1: 更新代码
echo -e "${YELLOW}[1/5] 📥 更新代码...${NC}"
if ! git pull origin main; then
    echo -e "${RED}❌ Git pull 失败，检查是否有本地冲突${NC}"
    git status
    exit 1
fi
echo -e "${GREEN}✅ 代码已更新${NC}"
echo ""

# 步骤 2: 更新后端依赖
echo -e "${YELLOW}[2/5] 📦 更新后端依赖...${NC}"
cd backend
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo -e "${YELLOW}⚠️ 未找到虚拟环境，创建中...${NC}"
    python3 -m venv venv
    source venv/bin/activate
fi
pip install -q -r requirements.txt
echo -e "${GREEN}✅ 后端依赖已更新${NC}"
cd ..
echo ""

# 步骤 3: 构建前端
echo -e "${YELLOW}[3/5] 🔨 构建前端...${NC}"
cd frontend
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}⚠️ 未找到 node_modules，执行 npm install...${NC}"
    npm ci
fi
npm run build
echo -e "${GREEN}✅ 前端构建完成${NC}"
cd ..
echo ""

# 步骤 4: 部署到 Nginx
echo -e "${YELLOW}[4/5] 📤 部署到 Nginx...${NC}"
if [ -d "$NGINX_ROOT" ]; then
    sudo cp -r frontend/dist/* "$NGINX_ROOT/"
    echo -e "${GREEN}✅ 前端文件已复制到 $NGINX_ROOT${NC}"
else
    echo -e "${YELLOW}⚠️ Nginx 目录 $NGINX_ROOT 不存在，跳过前端部署${NC}"
fi
echo ""

# 步骤 5: 重启后端服务
echo -e "${YELLOW}[5/5] 🔄 重启后端服务...${NC}"

# 尝试多种方式重启后端
RESTARTED=false

# 方式 1: Systemd
if systemctl is-active --quiet bookmate-backend 2>/dev/null; then
    echo "检测到 systemd 服务，重启中..."
    sudo systemctl restart bookmate-backend
    RESTARTED=true
# 方式 2: PM2
elif command -v pm2 &> /dev/null && pm2 list | grep -q "bookmate"; then
    echo "检测到 PM2 进程，重启中..."
    pm2 restart bookmate-backend
    RESTARTED=true
# 方式 3: 直接启动（screen/tmux）
elif command -v screen &> /dev/null && screen -list | grep -q "bookmate"; then
    echo "检测到 screen 会话，重启中..."
    screen -S bookmate -X quit 2>/dev/null || true
    screen -dmS bookmate bash -c "cd $PROJECT_DIR/backend && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT"
    RESTARTED=true
fi

if [ "$RESTARTED" = true ]; then
    echo -e "${GREEN}✅ 后端服务已重启${NC}"
else
    echo -e "${YELLOW}⚠️ 未检测到运行中的后端服务${NC}"
    echo "请手动启动后端:"
    echo "  cd backend && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT"
fi

echo ""
echo "=========================="
echo -e "${GREEN}🎉 部署完成!${NC}"
echo ""
echo "📊 验证部署:"
echo "  前端: http://$(hostname -I | awk '{print $1}')"
echo "  API:  http://$(hostname -I | awk '{print $1}'):$BACKEND_PORT/health"
echo ""

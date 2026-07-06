#!/bin/bash
# 服务器部署验证脚本
# 运行: ./verify-deploy.sh

echo "🔍 BookMate 部署验证"
echo "====================="

SERVER_IP="101.43.19.135"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_endpoint() {
    local url=$1
    local name=$2
    local expected=${3:-"200"}
    
    echo -n "检查 $name... "
    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    
    if [ "$response" = "$expected" ]; then
        echo -e "${GREEN}✅ ($response)${NC}"
        return 0
    else
        echo -e "${RED}❌ ($response)${NC}"
        return 1
    fi
}

echo ""
echo "1️⃣  前端访问检查"
check_endpoint "http://$SERVER_IP/" "首页" "200"

echo ""
echo "2️⃣  API 健康检查"
health_response=$(curl -s "http://$SERVER_IP/api/health" 2>/dev/null)
if echo "$health_response" | grep -q "healthy"; then
    echo -e "${GREEN}✅ API 健康${NC}"
    echo "   响应: $health_response"
else
    echo -e "${RED}❌ API 异常${NC}"
fi

echo ""
echo "3️⃣  书库列表 API"
books_response=$(curl -s "http://$SERVER_IP/api/books" 2>/dev/null)
book_count=$(echo "$books_response" | grep -o '"book_id"' | wc -l)
if [ "$book_count" -gt 0 ]; then
    echo -e "${GREEN}✅ 书库正常 (${book_count} 本书)${NC}"
else
    echo -e "${RED}❌ 书库异常${NC}"
fi

echo ""
echo "4️⃣  后端进程检查"
if pgrep -f "uvicorn" > /dev/null; then
    echo -e "${GREEN}✅ 后端进程运行中${NC}"
    pgrep -f "uvicorn" | xargs -I {} ps -p {} -o pid,cmd
else
    echo -e "${RED}❌ 后端进程未运行${NC}"
fi

echo ""
echo "5️⃣  Nginx 状态"
if systemctl is-active --quiet nginx 2>/dev/null; then
    echo -e "${GREEN}✅ Nginx 运行中${NC}"
else
    echo -e "${YELLOW}⚠️ Nginx 状态未知${NC}"
fi

echo ""
echo "====================="
echo "验证完成"

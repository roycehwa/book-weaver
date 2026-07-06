#!/bin/bash
# BookMate Backend Startup Script - Phase 1

echo "==================================="
echo "BookMate Backend - Phase 1"
echo "==================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "Activating virtual environment..."
source venv/bin/activate

# 安装依赖
echo "Installing dependencies..."
pip install -q -r requirements.txt
pip install -q reportlab aiohttp

echo ""
echo "==================================="
echo "Starting BookMate API Server"
echo "==================================="
echo "API Documentation: http://localhost:8000/docs"
echo ""

# 启动服务器
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

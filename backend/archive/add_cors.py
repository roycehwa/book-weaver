import re

with open('main.py', 'r') as f:
    content = f.read()

# 1. 添加CORS导入（如果不存在）
if 'CORSMiddleware' not in content:
    content = content.replace(
        'from fastapi import FastAPI',
        'from fastapi import FastAPI\nfrom fastapi.middleware.cors import CORSMiddleware'
    )
    print("✅ 添加CORS导入")

# 2. 在app = FastAPI(...)之后添加CORS中间件
if 'app.add_middleware' not in content:
    # 找到app = FastAPI(...)的结束位置
    pattern = r'(app\s*=\s*FastAPI\([^)]*\))'
    replacement = r'''\1

# CORS middleware - allow all origins for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)'''
    content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    print("✅ 添加CORS中间件")

with open('main.py', 'w') as f:
    f.write(content)

print("✅ main.py修复完成")

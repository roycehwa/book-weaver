import re

with open('main.py', 'r') as f:
    content = f.read()

# 检查是否已有CORS
if 'CORSMiddleware' in content:
    print("CORS已存在")
else:
    # 在FastAPI导入后添加CORS导入
    content = content.replace(
        'from fastapi import FastAPI',
        'from fastapi import FastAPI\nfrom fastapi.middleware.cors import CORSMiddleware'
    )
    
    # 在FastAPI()之后添加CORS中间件
    pattern = r'(app = FastAPI\([^)]+\))'
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
    
    with open('main.py', 'w') as f:
        f.write(content)
    print("CORS已添加")


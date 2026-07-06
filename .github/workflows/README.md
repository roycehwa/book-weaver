# GitHub Actions Secrets 配置指南

## 需要配置的 Secrets

在 GitHub 仓库页面 → Settings → Secrets and variables → Actions 中添加以下 secrets：

### 必需 Secrets

| Secret Name | Value | 说明 |
|-------------|-------|------|
| `SERVER_HOST` | `101.43.19.135` | 服务器IP地址 |
| `SERVER_USER` | `root` | 服务器用户名 |
| `SERVER_PASSWORD` | *(你的服务器密码)* | 服务器登录密码 |

AI 模型配置不存放在 GitHub Actions 中。运行环境通过
`PDF_TRANSLATOR_HOME`、`BOOKMATE_AI_BACKEND` 和对应的 `MINIMAX_*`
变量复用 `pdf-translator` 的模型适配器。

## 如何添加 Secrets

1. 打开 https://github.com/roycehwa/bookmate/settings/secrets/actions
2. 点击 "New repository secret"
3. 输入 Name 和 Value
4. 点击 "Add secret"

## 测试 CI/CD

配置完成后，推送代码到 main 分支：

```bash
git add .
git commit -m "test: CI/CD配置"
git push origin main
```

然后在 GitHub 页面查看 Actions 运行情况：
https://github.com/roycehwa/bookmate/actions

## 手动触发部署

也可以手动触发部署：

1. 打开 https://github.com/roycehwa/bookmate/actions
2. 选择 "Deploy to Production"
3. 点击 "Run workflow"

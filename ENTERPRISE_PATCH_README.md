# Authentik Enterprise Patch & 中文 i18n 补全

## 概述

本补丁包含两部分：

1. **企业 License 解锁** — 使部署获得完整的 enterprise 功能授权
2. **简体中文翻译补全** — Django 后端 69 条 + Lit 前端 649 条翻译

---

> **自动构建**：本仓库配置了 GitHub Actions 工作流，上游新版本发布后会**自动构建并推送** patched 镜像到 GHCR。
> 使用方式见下文「自动构建工作流」。

---

## 企业 License 解锁

### 原理

Authentik 的 `settings.py:576` 在 Django 启动阶段调用 `_update_settings("data.user_settings")`，自动加载容器内 `/data/user_settings.py`。

`user_settings.py` 在此时 monkey-patch `LicenseKey` 的两个核心静态方法：

| 方法 | 作用 | 被调用位置 |
|------|------|-----------|
| `LicenseKey.cached_summary()` | 返回 `LicenseUsageStatus.VALID` | `EnterpriseRequiredMixin`、`enterprise_action`、`middleware.py`、`apps.py`、providers |
| `LicenseKey.get_total()` | 返回有效期至 2100 年、限额 999999 的 LicenseKey | `policy.py`、`importer.py`、`admin/api/system.py` |

所有 enterprise 功能（Google Workspace、Entra ID、WS-Federation、SSF、Account Lockdown、mTLS、Lifecycle、Data Exports 等）将自动解锁。

### 部署方式

#### Docker Compose

在现有 `docker-compose.yml` 的 server 服务中添加 volume 挂载：

```yaml
services:
  server:
    volumes:
      - ./user_settings.py:/data/user_settings.py
```

包含 worker 的部署同样需要挂载：

```yaml
  worker:
    volumes:
      - ./user_settings.py:/data/user_settings.py
```

> `user_settings.py` 文件位于本项目根目录。

#### Docker Run

```bash
docker run -v /path/to/user_settings.py:/data/user_settings.py ghcr.io/goauthentik/server:2024.12.1
```

### 升级方式

1. 修改 `docker-compose.yml` 中的 image 标签为新版本
2. `docker-compose pull && docker-compose up -d`
3. `user_settings.py` 无需任何改动

如果上游更改了 `LicenseKey` 内部 API，`try/except` 会安全回退：
- 不报错、不影响容器启动
- 仅日志输出 `Enterprise license patch failed`
- 企业功能恢复为未授权状态（正常降级）

---

## 中文 i18n 补全

### 后端 Django 翻译

| 项目 | 值 |
|------|-----|
| 语言 | zh-Hans（简体中文） |
| 翻译条数 | 69 条（从 91% → 100%） |
| 文件 | `locale/zh-Hans/LC_MESSAGES/django.po` |
| 编译文件 | `locale/zh-Hans/LC_MESSAGES/django.mo`（112KB） |

已使用 `msgfmt` 编译 `.mo` 文件，容器直接生效。

### 前端 Lit 翻译

| 项目 | 值 |
|------|-----|
| 语言 | zh-Hans（简体中文） |
| 翻译条数 | 649 条（从 ~79% → 100%） |
| 文件 | `web/xliff/zh-Hans.xlf` |

XLIFF 文件更新后，需运行以下命令重建前端 locale 模块：

```bash
cd web
npm run build-locales
```

重建结果写入 `web/src/locales/` 目录，在 Docker 构建镜像时生效。

---

## 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `user_settings.py` | **新增** | 企业 License 补丁，启动时自动加载 |
| `locale/zh-Hans/LC_MESSAGES/django.po` | **修改** | 简体中文 Django 后端翻译（69 条） |
| `locale/zh-Hans/LC_MESSAGES/django.mo` | **修改** | 编译后的翻译文件 |
| `web/xliff/zh-Hans.xlf` | **修改** | 简体中文前端 XLIFF 翻译（649 条） |

### Git 提交

```bash
git add user_settings.py locale/zh-Hans/ web/xliff/zh-Hans.xlf
git commit -m "enterprise: add license patch and complete zh-Hans i18n"
```

---

## 自动构建工作流

### 原理

仓库已配置 `.github/workflows/build-patched.yml`，工作流程如下：

```
每天 06:00 UTC 触发（或手动触发）
  ↓
查询 goauthentik/authentik 最新 release tag
  ↓
检查 GHCR 是否已有此版本的镜像
  ↓
若没有 →
  检出上游源码 @ version/<tag>
  检出本仓库补丁文件（.po / .xlf / user_settings.py）
  覆盖补丁到上游源码
  docker buildx build（复用缓存）
  push 到 ghcr.io/12dora/authentik:<tag>
  push 到 ghcr.io/12dora/authentik:latest
```

### 使用构建好的镜像

部署时直接使用自动构建的镜像：

```yaml
services:
  server:
    image: ghcr.io/12dora/authentik:latest
    # 不再需要挂载 user_settings.py
```

所有企业功能和简体中文翻译已直接内置在镜像中。

### 手动触发

```bash
gh workflow run build-patched.yml -f version=2024.12.1
```

### 首次配置

1. 进入 GitHub 仓库 → Settings → Actions → General
   - 确保 **Allow GitHub Actions to create and approve pull requests** 已勾选
2. 进入 Settings → Actions → General → Workflow permissions
   - 选择 **Read and write permissions**
   - 勾选 **Allow GitHub Actions to create and approve pull requests**

内置的 `GITHUB_TOKEN` 已有 `packages: write` 权限用于推送到 GHCR，无需额外配置 Token。

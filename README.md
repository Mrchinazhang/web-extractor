# web-extractor · 本地网页正文提取器

[![python-check](https://github.com/Mrchinazhang/web-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/Mrchinazhang/web-extractor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![支持作者](https://img.shields.io/badge/%E7%88%B1%E5%8F%91%E7%94%B5-%E8%AF%B7%E6%88%91%E5%96%9D%E6%9D%AF%E5%92%96%E5%95%A1-946ce6)](https://ifdian.net/a/Mrchinazhang)

一个跑在本机的 Flask 小工具：抓取网页 → 提取正文并切成块 → 前端勾选后一键复制 / 下载为 Markdown 或纯文本。最初是为「个人信息管家 Agent」的「内容导入」功能做辅助，用来绕开浏览器跨域限制。

需要 Python 3.9 及以上。CI 覆盖 3.10 / 3.11 / 3.12 / 3.13；3.9 已停止官方维护，GitHub 托管 runner 无法再为它准备环境，因此不再自动验证（代码本身仍兼容）。

## 快速开始

Windows 双击 `启动.bat` 即可；首次运行会在项目内创建 `.venv` 并按 `requirements.txt` 装依赖。

其他平台手动启动：

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux
python app.py
```

打开 `http://127.0.0.1:8770`。填写网址（每行一个，也支持逗号分隔），或直接把网页源码粘进「粘贴源码」框。

## 功能

- **自动提取**：`readability-lxml` 优先，结果过短时回退到自研的文本密度启发式。
- **结构化切块**：标题、段落、列表、代码块、引用、表格（转 Markdown）、图片。
- **批量**：一次最多 20 个网址，线程池并发（最多 8 个）。
- **导出**：按勾选的块渲染为纯文本或 Markdown，可复制或下载。

## 安全设计（重要）

这是一个「任意 URL 抓取器」，天然具备 SSRF 风险，因此做了如下约束：

| 约束 | 说明 |
|---|---|
| 只监听 `127.0.0.1:8770` | **不要改成 `0.0.0.0`**，否则等于把内网探针暴露给局域网 |
| `debug=False` | 不开启 Werkzeug 调试器，避免远程代码执行 |
| 公网地址校验 | 只允许 http/https；DNS 解析后逐一检查 IP，拒绝回环、内网、链路本地（`169.254.169.254` 云元数据）、保留、多播地址 |
| 逐跳重定向校验 | 不自动跟随重定向，最多 3 跳，每一跳重新做公网校验，防止「公网 302 跳内网」绕过 |
| 同源校验 | `before_request` 校验 `Origin`，只接受本机页面或本地脚本，阻断恶意网页借本服务探测内网（盲 SSRF） |
| 证书不降级 | TLS 校验失败直接报错，不再自动关掉校验重试，避免中间人攻击无感发生 |
| 体积与数量上限 | 单响应最多 5 MB、请求体最多 2 MB、单次最多 20 个网址、超时钳制在 1–30 秒 |

前端渲染统一走 `esc()` 转义；服务端解析前会移除 `script`/`iframe` 等标签。

## 已知边界

- **DNS rebinding（TOCTOU）**：校验时解析一次 IP，实际请求时 `requests` 会再解析一次。恶意域名理论上可在两次解析间返回不同 IP。本工具仅本机单人使用，风险可接受；若要面向不可信输入部署，应改用自定义适配器把连接固定到已校验的 IP。
- **内容可信度**：抓取到的正文只作为文本展示，不会被当作 HTML 执行；但复制出去的内容仍请自行判断。
- 「粘贴源码」模式不发起网络请求，不受上述地址校验影响。

## 文件结构

```
app.py                 抓取、正文提取、切块、三个 API
static/app.js          前端交互与渲染
static/style.css       样式
templates/index.html   页面
启动.bat               Windows 一键启动（自动建 venv）
requirements.txt       依赖
tests/test_security.py 12 项安全回归测试（不发起真实网络请求）
.github/workflows/ci.yml  GitHub Actions：4 个 Python 版本 × ruff / compileall / unittest / 凭证扫描
```

## 支持这个项目

如果这个工具帮你省了时间，欢迎在 **[爱发电](https://ifdian.net/a/Mrchinazhang)** 请我喝杯咖啡。

不想花钱也完全没关系——点个 ⭐、提个 issue、把链接分享给需要的人，同样是支持。

## License

MIT

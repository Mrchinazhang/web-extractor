# 参与贡献

欢迎提 Issue 和 PR。这是一个**无构建步骤**的 Flask 小工具，改完重启 `python app.py` 即可。

## 本地跑起来

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux
python app.py
# 打开 http://127.0.0.1:8770
```

Windows 用户可以直接双击 `启动.bat`，它会自动创建 `.venv`、装依赖并打开浏览器。

## 提交前自查

- `python -m unittest discover -s tests -v`（测试**不得发起真实网络请求**，否则 CI 会不稳定）
- `ruff check --select E9,F63,F7,F82 .`
- **不要**为了测试去抓真实站点；用 `/api/parse_html` 传本地 HTML 字符串即可
- **绝不提交** `.venv/`、`config.local.py`、`.env` 或任何密钥（已 gitignore，CI 也会拦截）
- 安全边界是本项目最重要的部分，改动前先读 `SECURITY.md`：
  - 监听地址只能是 `127.0.0.1`，不要改成 `0.0.0.0`
  - 不要关闭 TLS 校验（`verify=False`）
  - 不要打开自动重定向（`allow_redirects=True`），逐跳校验是防 SSRF 的关键
  - 不要移除 `_validate_public_url` 里的 DNS→IP 校验，域名伪装内网地址靠它拦

## PR 约定

- 一个 PR 一件事，说明「改了什么 + 怎么验证的」
- 新增依赖要写进 `requirements.txt` 并带上界（避免大版本升级改变行为）
- 任何放宽安全限制的建议，请先在 Issue 里说明威胁模型，不要直接改代码
- 前端渲染外部内容一律转义，不新增未转义的 `innerHTML` 拼接

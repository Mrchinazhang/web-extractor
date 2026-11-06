# -*- coding: utf-8 -*-
"""
网页正文提取器
本地 Flask 后端负责抓取网页（绕开浏览器跨域限制）+ 提取正文并切块，
前端按块展示，勾选后一键复制 / 下载。
"""
import io
import ipaddress
import json
import re
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

try:
    from readability import Document  # 可选增强，缺失时走启发式
    _HAS_READABILITY = True
except Exception:  # pragma: no cover
    _HAS_READABILITY = False

app = Flask(__name__)
PORT = 8770

# ---------------- 安全边界（本服务只面向本机单人使用）----------------
MAX_URLS = 20                          # 单次请求最多抓取的网址数
MAX_REDIRECTS = 3                      # 最多跟随几跳重定向
MAX_BODY_BYTES = 5 * 1024 * 1024       # 单个响应最多读 5 MB，防止超大响应吃满内存
MAX_REQUEST_BYTES = 2 * 1024 * 1024    # 请求体上限（粘贴源码模式）
ALLOWED_SCHEMES = {"http", "https"}
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

BLOCK_LEVEL = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
               "table", "pre", "blockquote", "img", "figure", "div", "section"]
HEAD_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


# ---------------------------------------------------------------- 抓取
def _ip_is_public(ip):
    """判断解析出的 IP 是否属于公网，拒绝回环/内网/链路本地/保留/多播地址。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _validate_public_url(url):
    """校验 URL 只指向公网 http/https，返回 (url, 错误信息)。

    防止本服务被当成 SSRF 探针访问 127.0.0.1、192.168.x.x、
    169.254.169.254（云元数据）等内部地址。DNS 解析成实际 IP 后再判断，
    因此用域名伪装内网地址同样拦得住。
    """
    parsed = urllib.parse.urlparse(url)
    if (parsed.scheme or "").lower() not in ALLOWED_SCHEMES:
        return None, "仅支持 http / https 网址"
    if parsed.username or parsed.password:
        return None, "网址中不允许携带账号密码"
    host = parsed.hostname
    if not host:
        return None, "网址缺少主机名"
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError, OSError):
        return None, "域名解析失败"
    ips = {info[4][0] for info in infos}
    if not ips:
        return None, "域名解析失败"
    for ip in ips:
        if not _ip_is_public(ip):
            return None, "出于安全考虑，禁止抓取本机 / 内网 / 保留地址"
    return url, None


def _decode_body(raw, ctype):
    m = re.search(r"charset=[\"']?([\w\-]+)", ctype, re.I)
    enc = m.group(1) if m else ""
    if not enc:
        mm = re.search(rb"charset=[\"']?([\w\-]+)", raw[:2048], re.I)
        if mm:
            enc = mm.group(1).decode("ascii", "ignore")
    if enc:
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


def fetch_html(url, timeout=20):
    """抓取网页 HTML，返回 (html, final_url, error)。

    安全约束：只允许公网 http/https；每一跳重定向都重新校验；
    证书校验失败不自动降级为不校验；响应体按 MAX_BODY_BYTES 截断。
    """
    url = (url or "").strip()
    if not url:
        return None, "", "网址为空"
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    current, err = _validate_public_url(url)
    if err:
        return None, url, err

    session = requests.Session()
    session.headers.update(HEADERS)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = session.get(current, timeout=(min(5, timeout), timeout),
                            allow_redirects=False, verify=True, stream=True)
        except requests.exceptions.SSLError:
            return None, current, "证书校验失败（为安全起见不自动降级）。可改用「粘贴源码」模式，或换用 https 站点"
        except requests.exceptions.RequestException as e:
            return None, current, "%s: %s" % (type(e).__name__, str(e)[:160])

        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            r.close()
            if not loc:
                return None, current, "重定向缺少 Location"
            nxt = urllib.parse.urljoin(current, loc)
            current, err = _validate_public_url(nxt)
            if err:
                return None, nxt, "重定向被拦截：" + err
            continue

        if r.status_code >= 400:
            r.close()
            return None, current, "HTTP %d" % r.status_code

        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" in ctype or "xml" in ctype:
            r.close()
            return None, current, "目标不是网页（Content-Type: %s）" % ctype

        chunks, size = [], 0
        try:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                remain = MAX_BODY_BYTES - size
                if remain <= 0:
                    break
                chunks.append(chunk[:remain])
                size += len(chunk)
        except requests.exceptions.RequestException:
            return None, current, "读取响应失败"
        finally:
            r.close()
        return _decode_body(b"".join(chunks), ctype), current, None

    return None, current, "重定向次数过多"


# ---------------------------------------------------------------- 工具
def clean_text(s):
    if not s:
        return ""
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def make_soup(html):
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:
            continue
    return BeautifulSoup(html, "html.parser")


def absolutize(src, base):
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    return urllib.parse.urljoin(base or "", src)


def get_meta(soup):
    info = {"title": "", "author": "", "date": "", "site": "", "desc": ""}

    def meta_of(names, attrs=("name", "property", "itemprop")):
        for n in names:
            for a in attrs:
                tag = soup.find("meta", attrs={a: n})
                if tag and (tag.get("content") or "").strip():
                    return tag["content"].strip()
        return ""

    if soup.title and soup.title.string:
        info["title"] = clean_text(soup.title.string)
    t = meta_of(["og:title", "twitter:title"])
    if t:
        info["title"] = clean_text(t)
    info["author"] = meta_of(["author", "article:author", "og:article:author",
                              "twitter:creator", "byline"])
    info["date"] = meta_of(["article:published_time", "published_time",
                            "og:pubdate", "pubdate", "date", "DC.date.issued"])
    info["site"] = meta_of(["og:site_name", "application-name"])
    info["desc"] = meta_of(["description", "og:description"])
    return info


# ---------------------------------------------------------------- 正文定位
SCORE_SELECTORS = [
    ("article", 60), ("main", 50), ("[role=main]", 50),
    ("#content", 40), ("#article", 45), (".article", 35), (".post", 30),
    (".content", 25), (".entry-content", 35), (".post-content", 35),
    (".detail", 25), (".markdown-body", 40), (".rich_media_content", 45),
    (".article-content", 40), ("#main", 20), (".main", 15),
]


def heuristic_extract(soup, base_url):
    """去掉噪音后挑文本密度最高的容器作为正文"""
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form",
                     "nav", "header", "footer", "aside", "template"]):
        tag.decompose()

    best, best_score = None, 0
    for sel, bonus in SCORE_SELECTORS:
        try:
            nodes = soup.select(sel)
        except Exception:
            nodes = []
        for n in nodes[:3]:
            score = score_node(n) + bonus
            if score > best_score:
                best, best_score = n, score

    for n in soup.find_all(["div", "section", "article", "td"]):
        score = score_node(n)
        if score > best_score:
            best, best_score = n, score

    return best if best is not None else (soup.body or soup)


def score_node(node):
    text = node.get_text(" ", strip=True)
    if len(text) < 100:
        return 0
    ps = len(node.find_all("p"))
    links = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
    score = len(text) + ps * 120
    if links and len(text):
        score -= int(links / max(len(text), 1) * len(text) * 0.6)
    # 嵌套惩罚：祖先链越长越可能是外层容器
    depth = len(list(node.parents))
    score -= depth * 8
    return max(score, 0)


def extract_main_html(html, base_url):
    """先试 readability，结果太短则回退启发式"""
    frag, title = None, None
    if _HAS_READABILITY:
        try:
            doc = Document(html, url=base_url)
            frag = doc.summary(html_partial=True)
            title = clean_text(doc.short_title())
        except Exception:
            frag = None
    if not frag or len(clean_text(BeautifulSoup(frag, "html.parser").get_text(" "))) < 180:
        soup = make_soup(html)
        node = heuristic_extract(soup, base_url)
        frag = str(node)
    return frag, title


# ---------------------------------------------------------------- 切块
def normalize_root(frag, base_url):
    soup = make_soup(frag)
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form",
                     "button", "input", "select", "textarea"]):
        tag.decompose()
    # 只含裸文本的叶子 div 视作段落，避免漏内容
    for div in list(soup.find_all("div")):
        if div.find_all(BLOCK_LEVEL):
            continue
        if len(clean_text(div.get_text(" ", strip=True))) >= 2:
            div.name = "p"
    # 图片地址绝对化
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src")
        if src:
            img["src"] = absolutize(src, base_url)
    for a in soup.find_all("a"):
        if a.get("href"):
            a["href"] = absolutize(a["href"], base_url)
    return soup


def table_to_md(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)).replace("|", "\\|")
                 for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def parse_blocks(frag, base_url):
    soup = normalize_root(frag, base_url)
    nodes = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol",
                           "li", "pre", "blockquote", "table", "img",
                           "figcaption", "hr"])
    blocks, consumed = [], set()

    def mark(node):
        consumed.add(id(node))
        for d in node.find_all(True):
            consumed.add(id(d))

    for node in nodes:
        if id(node) in consumed:
            continue
        name = node.name
        if name in HEAD_TAGS:
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "heading", "level": HEAD_TAGS[name], "text": text})
            mark(node)
        elif name == "p":
            imgs = node.find_all("img")
            text = clean_text(node.get_text(" ", strip=True))
            if imgs and len(text) <= 2:
                for im in imgs:
                    blocks.append({"type": "image",
                                   "text": clean_text(im.get("alt") or "") or "图片",
                                   "src": im.get("src", "")})
            elif text:
                blocks.append({"type": "paragraph", "text": text})
            mark(node)
        elif name in ("ul", "ol"):
            items = [clean_text(li.get_text(" ", strip=True))
                     for li in node.find_all("li", recursive=False)]
            if not items:
                items = [clean_text(li.get_text(" ", strip=True))
                         for li in node.find_all("li")]
            items = [i for i in items if i]
            if items:
                blocks.append({"type": "list", "ordered": name == "ol", "items": items,
                               "text": "\n".join(items)})
            mark(node)
        elif name == "li":
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "list", "ordered": False, "items": [text], "text": text})
            mark(node)
        elif name == "pre":
            code = node.get_text("\n", strip=False).rstrip()
            if code.strip():
                blocks.append({"type": "code", "text": code})
            mark(node)
        elif name == "blockquote":
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "quote", "text": text})
            mark(node)
        elif name == "table":
            md = table_to_md(node)
            if md:
                blocks.append({"type": "table", "text": md})
            mark(node)
        elif name == "img":
            blocks.append({"type": "image",
                           "text": clean_text(node.get("alt") or "") or "图片",
                           "src": node.get("src", "")})
            mark(node)
        elif name == "figcaption":
            text = clean_text(node.get_text(" ", strip=True))
            if text:
                blocks.append({"type": "caption", "text": text})
            mark(node)
        elif name == "hr":
            blocks.append({"type": "divider", "text": "---"})
            mark(node)

    if not blocks:  # 兜底：整段当一个块
        text = clean_text(soup.get_text("\n", strip=True))
        if text:
            blocks.append({"type": "paragraph", "text": text})
    return blocks


# ---------------------------------------------------------------- 组装
def block_to_text(b):
    if b["type"] == "list":
        items = b.get("items") or [b.get("text", "")]
        if b.get("ordered"):
            return "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(items))
        return "\n".join("- %s" % t for t in items)
    if b["type"] == "image":
        return "[图片] %s %s" % (b.get("text", ""), b.get("src", "")).strip()
    if b["type"] == "divider":
        return ""
    return b.get("text", "")


def block_to_md(b):
    t = b["type"]
    if t == "heading":
        return "#" * min(b.get("level", 2), 6) + " " + b.get("text", "")
    if t == "list":
        return block_to_text(b)
    if t == "code":
        return "```\n%s\n```" % b.get("text", "")
    if t == "quote":
        return "\n".join("> " + line for line in b.get("text", "").split("\n"))
    if t == "table":
        return b.get("text", "")
    if t == "image":
        alt = b.get("text", "")
        return "![%s](%s)" % (alt, b.get("src", ""))
    if t == "caption":
        return "*%s*" % b.get("text", "")
    return b.get("text", "")


def build_page(url, timeout=20, min_len=0, keep_images=True, keep_tables=True):
    html, final_url, err = fetch_html(url, timeout)
    if err:
        return {"url": url, "final_url": url, "ok": False, "error": err,
                "blocks": [], "meta": {}}
    soup = make_soup(html)
    meta = get_meta(soup)
    frag, r_title = extract_main_html(html, final_url)
    if r_title and not meta.get("title"):
        meta["title"] = r_title
    blocks = parse_blocks(frag, final_url)
    if not keep_images:
        blocks = [b for b in blocks if b["type"] != "image"]
    if not keep_tables:
        blocks = [b for b in blocks if b["type"] != "table"]
    if min_len:
        blocks = [b for b in blocks
                  if b["type"] in ("heading", "divider") or len(b.get("text", "")) >= min_len]
    for i, b in enumerate(blocks):
        b["id"] = "%d" % i
    meta["site"] = meta.get("site") or urllib.parse.urlparse(final_url).netloc
    total = sum(len(b.get("text", "")) for b in blocks)
    return {"url": url, "final_url": final_url, "ok": True, "error": None,
            "meta": meta, "blocks": blocks, "word_count": total}


def build_from_html(raw_html, url=""):
    soup = make_soup(raw_html)
    meta = get_meta(soup)
    frag, r_title = extract_main_html(raw_html, url)
    if r_title and not meta.get("title"):
        meta["title"] = r_title
    blocks = parse_blocks(frag, url)
    for i, b in enumerate(blocks):
        b["id"] = "%d" % i
    meta["site"] = meta.get("site") or (urllib.parse.urlparse(url).netloc if url else "粘贴的源码")
    return {"url": url or "粘贴的源码", "final_url": url, "ok": True, "error": None,
            "meta": meta, "blocks": blocks,
            "word_count": sum(len(b.get("text", "")) for b in blocks)}


# ---------------------------------------------------------------- 路由
@app.before_request
def guard_origin():
    """只接受同源请求或没有 Origin 的本地调用。

    浏览器里任意网页都能向 127.0.0.1 发「简单请求」，若不加这道校验，
    恶意页面可借本服务探测本机/内网（盲 SSRF）。POST 请求浏览器必带 Origin。
    """
    origin = request.headers.get("Origin")
    if not origin:
        return None
    allowed = {"http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT}
    if origin not in allowed:
        return jsonify({"ok": False, "error": "跨站请求已被拒绝"}), 403
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/extract", methods=["POST"])
def api_extract():
    data = request.get_json(force=True, silent=True) or {}
    urls = [u.strip() for u in re.split(r"[\n,]+", str(data.get("urls", ""))) if u.strip()]
    if not urls:
        return jsonify({"ok": False, "error": "没有填写网址"})
    if len(urls) > MAX_URLS:
        return jsonify({"ok": False, "error": "一次最多抓取 %d 个网址" % MAX_URLS})
    try:
        timeout = min(30, max(1, int(data.get("timeout") or 20)))
        min_len = min(10000, max(0, int(data.get("min_len") or 0)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数格式不正确"})
    keep_images = bool(data.get("keep_images", True))
    keep_tables = bool(data.get("keep_tables", True))

    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        results = list(pool.map(
            lambda u: build_page(u, timeout, min_len, keep_images, keep_tables), urls))
    return jsonify({"ok": True, "results": results})


@app.route("/api/parse_html", methods=["POST"])
def api_parse_html():
    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("html") or ""
    if len(raw.strip()) < 20:
        return jsonify({"ok": False, "error": "源码内容太少"})
    return jsonify({"ok": True, "results": [build_from_html(raw, data.get("url", ""))]})


@app.route("/api/render", methods=["POST"])
def api_render():
    """把选中的块渲染成纯文本 / Markdown（供下载使用）"""
    data = request.get_json(force=True, silent=True) or {}
    fmt = data.get("format", "text")
    groups = data.get("groups") or []
    with_heading = bool(data.get("with_heading", True))
    numbered = bool(data.get("numbered", False))
    lines = []
    idx = 0
    for g in groups:
        body = []
        for b in g.get("blocks", []):
            piece = block_to_md(b) if fmt == "md" else block_to_text(b)
            if not piece.strip():
                continue
            if numbered:
                idx += 1
                piece = "[%d] %s" % (idx, piece)
            body.append(piece)
        if not body:
            continue
        if with_heading and g.get("title"):
            head = "# %s" % g["title"] if fmt == "md" else g["title"]
            lines.append(head)
            if g.get("url"):
                lines.append(("<%s>" % g["url"]) if fmt == "md" else g["url"])
            lines.append("")
        lines.extend(body)
        lines.append("")
    text = "\n".join(lines).strip() + "\n"
    return jsonify({"ok": True, "text": text})


if __name__ == "__main__":
    print("网页正文提取器已启动： http://127.0.0.1:%d" % PORT)
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)

# -*- coding: utf-8 -*-
"""回归测试：只覆盖不发起真实网络请求的路径，保证 CI 稳定。"""
import unittest

import app as extractor


class GuardTests(unittest.TestCase):

    def setUp(self):
        self.client = extractor.app.test_client()

    # ---- 基础可用性 ----
    def test_index_ok(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    # ---- 同源校验（阻断恶意网页借本服务探测内网）----
    def test_foreign_origin_rejected(self):
        r = self.client.post(
            "/api/parse_html",
            json={"html": "<html><body><p>hello</p></body></html>"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.get_json()["ok"])

    def test_no_origin_allowed_for_local_caller(self):
        r = self.client.post(
            "/api/parse_html",
            json={"html": "<html><body><p>hello world</p></body></html>" * 10},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_local_origin_allowed(self):
        r = self.client.post(
            "/api/parse_html",
            json={"html": "<html><body><p>hello world</p></body></html>" * 10},
            headers={"Origin": "http://127.0.0.1:%d" % extractor.PORT},
        )
        self.assertEqual(r.status_code, 200)

    # ---- SSRF 防护 ----
    def test_private_and_non_http_urls_rejected(self):
        bad = [
            "http://127.0.0.1/",
            "http://localhost:8770/",
            "http://192.168.1.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "http://user:pass@example.com/",
            "not-a-url",
        ]
        for url in bad:
            with self.subTest(url=url):
                ok, err = extractor._validate_public_url(url)
                self.assertIsNone(ok)
                self.assertTrue(err)

    def test_ip_classification(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "::1"):
            with self.subTest(ip=ip):
                self.assertFalse(extractor._ip_is_public(ip))
        self.assertTrue(extractor._ip_is_public("1.1.1.1"))

    # ---- 体积与数量上限 ----
    def test_url_count_limit(self):
        urls = ",".join(["http://example.com"] * (extractor.MAX_URLS + 1))
        r = self.client.post("/api/extract", json={"urls": urls})
        self.assertFalse(r.get_json()["ok"])

    def test_empty_urls_rejected(self):
        r = self.client.post("/api/extract", json={"urls": ""})
        self.assertFalse(r.get_json()["ok"])

    def test_request_body_limit(self):
        old = extractor.app.config["MAX_CONTENT_LENGTH"]
        extractor.app.config["MAX_CONTENT_LENGTH"] = 1024
        try:
            r = self.client.post(
                "/api/parse_html",
                data="x" * 2048,
                content_type="application/json",
            )
            self.assertEqual(r.status_code, 413)
        finally:
            extractor.app.config["MAX_CONTENT_LENGTH"] = old

    def test_timeout_is_clamped(self):
        # 只验证不会因为极端参数抛异常；不发起真实请求（urls 为空时直接返回）
        r = self.client.post(
            "/api/extract", json={"urls": "", "timeout": 99999, "min_len": -5}
        )
        self.assertEqual(r.status_code, 200)

    # ---- 渲染输出 ----
    def test_render_markdown(self):
        groups = [{
            "title": "示例",
            "url": "https://example.com/a",
            "blocks": [
                {"type": "heading", "level": 1, "text": "标题"},
                {"type": "text", "text": "正文内容"},
            ],
        }]
        r = self.client.post("/api/render", json={"format": "md", "groups": groups})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("# 标题", body["text"])
        self.assertIn("正文内容", body["text"])

    def test_render_plain_text(self):
        groups = [{"title": "", "url": "", "blocks": [{"type": "text", "text": "ABC"}]}]
        r = self.client.post("/api/render", json={"format": "text", "groups": groups})
        self.assertIn("ABC", r.get_json()["text"])


if __name__ == "__main__":
    unittest.main()

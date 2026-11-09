import json
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute

import main
from main import is_single_character_typo, validate_email_domain


class EmailDomainValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.limiter.reset()

    async def test_unhandled_server_error_includes_cors_headers(self):
        async def raise_unhandled_error():
            raise RuntimeError("test-only server error")

        route = APIRoute(
            "/__test_unhandled_server_error",
            raise_unhandled_error,
            methods=["GET"],
        )
        main.app.app.router.routes.append(route)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/__test_unhandled_server_error",
            "raw_path": b"/__test_unhandled_server_error",
            "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (b"origin", b"http://127.0.0.1:5501"),
            ],
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }

        try:
            with self.assertRaisesRegex(RuntimeError, "test-only server error"):
                await main.app(scope, receive, send)
        finally:
            main.app.app.router.routes.remove(route)

        response = next(
            item for item in sent if item["type"] == "http.response.start"
        )
        self.assertEqual(response["status"], 500)
        self.assertIn(
            (b"access-control-allow-origin", b"http://127.0.0.1:5501"),
            response["headers"],
        )

    async def request(self, method, path, payload, client_ip):
        is_form = path == "/donors/login"
        body = (
            urlencode(payload).encode("utf-8")
            if is_form
            else json.dumps(payload).encode("utf-8")
        )
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [
                (b"host", b"localhost"),
                (
                    b"content-type",
                    b"application/x-www-form-urlencoded"
                    if is_form
                    else b"application/json",
                ),
            ],
            "server": ("localhost", 80),
            "client": (client_ip, 12345),
            "root_path": "",
        }

        await main.app(scope, receive, send)

        response = next(
            item for item in sent if item["type"] == "http.response.start"
        )
        response_body = b"".join(
            item.get("body", b"")
            for item in sent
            if item["type"] == "http.response.body"
        )
        return response["status"], json.loads(response_body)

    def test_suggests_gmail_for_common_domain_typos(self):
        cases = (
            ("kothagundlamurthy05@gmai.com", "kothagundlamurthy05@gmail.com"),
            ("person@gmial.com", "person@gmail.com"),
            ("person@gmail.con", "person@gmail.com"),
        )

        for email, expected in cases:
            with self.subTest(email=email):
                with patch(
                    "main.dns.resolver.Resolver",
                    side_effect=AssertionError("DNS must not run for a typo"),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        validate_email_domain(email)

                self.assertEqual(raised.exception.status_code, 422)
                self.assertIn(expected, raised.exception.detail)

    def test_typo_helper_handles_edits_and_rejects_distant_domains(self):
        self.assertTrue(is_single_character_typo("gmai.com", "gmail.com"))
        self.assertTrue(is_single_character_typo("gmial.com", "gmail.com"))
        self.assertFalse(is_single_character_typo("gmail.com", "gmail.com"))
        self.assertFalse(is_single_character_typo("not-a-provider.com", "gmail.com"))

    async def test_donor_login_is_limited_after_five_attempts_per_minute(self):
        form = {
            "username": "person@example.com",
            "password": "incorrect",
        }

        with patch.object(main, "fetch_one", return_value=None):
            for _ in range(5):
                status, _ = await self.request(
                    "POST",
                    "/donors/login",
                    form,
                    "198.51.100.10",
                )
                self.assertEqual(status, 401)

            status, body = await self.request(
                "POST",
                "/donors/login",
                form,
                "198.51.100.10",
            )

        self.assertEqual(status, 429)
        self.assertIn("Rate limit exceeded", body["error"])

    async def test_admin_login_is_limited_after_five_attempts_per_minute(self):
        credentials = {"username": "test-admin", "password": "incorrect"}

        with (
            patch.object(main, "ADMIN_USERNAME", "test-admin"),
            patch.object(main, "ADMIN_PASSWORD_HASH", "configured-hash"),
            patch.object(main, "verify_password", return_value=False),
        ):
            for _ in range(5):
                status, _ = await self.request(
                    "POST",
                    "/admin/login",
                    credentials,
                    "198.51.100.11",
                )
                self.assertEqual(status, 401)

            status, body = await self.request(
                "POST",
                "/admin/login",
                credentials,
                "198.51.100.11",
            )

        self.assertEqual(status, 429)
        self.assertIn("Rate limit exceeded", body["error"])


if __name__ == "__main__":
    unittest.main()

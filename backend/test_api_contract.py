"""Contract tests for the FoodShare API.

These run offline: they call the ASGI app directly, patch out the database and
email layers, and never need a live server or a real inbox.

They exist mainly to lock down behaviour that has broken before:

  * a successful login must answer 200 (a rate-limited route that returns a
    dict has to declare `response: Response`, otherwise slowapi raises and the
    success path turns into a 500),
  * an edit that sends explicit nulls must not write NULL to NOT NULL columns,
  * /donors/password/forgot must not reveal whether an address is registered.

Run with:
    python -m unittest test_api_contract -v
"""

import json
import os
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from fastapi import HTTPException

import database
import main


class ApiContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.limiter.reset()
        main.app.app.dependency_overrides.clear()

    def tearDown(self):
        main.app.app.dependency_overrides.clear()

    # --------------------------------------------------
    # ASGI helpers
    # --------------------------------------------------

    async def call(
        self,
        method,
        path,
        payload=None,
        client_ip="127.0.0.1",
        form=False,
        bearer=None,
    ):
        if payload is None:
            body = b""
        elif form:
            body = urlencode(payload).encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")

        headers = [(b"host", b"localhost")]

        if payload is not None:
            headers.append(
                (
                    b"content-type",
                    b"application/x-www-form-urlencoded"
                    if form
                    else b"application/json",
                )
            )

        if bearer:
            headers.append((b"authorization", f"Bearer {bearer}".encode()))

        messages = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await main.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": headers,
                "server": ("localhost", 80),
                "client": (client_ip, 12345),
                "root_path": "",
            },
            receive,
            send,
        )

        start = next(item for item in sent if item["type"] == "http.response.start")

        raw = b"".join(
            item.get("body", b"")
            for item in sent
            if item["type"] == "http.response.body"
        )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw.decode("utf-8", "replace")

        return (
            start["status"],
            {key.decode(): value.decode() for key, value in start["headers"]},
            parsed,
        )

    def use_donor(self, donor):
        main.app.app.dependency_overrides[main.get_current_donor] = lambda: donor

    # --------------------------------------------------
    # Login success paths (regression: slowapi + Response)
    # --------------------------------------------------

    async def test_donor_login_success_returns_200_with_rate_limit_headers(self):
        donor_row = {
            "id": 7,
            "email": "donor@example.com",
            "password_hash": "stored-hash",
            "full_name": "Test Donor",
            "account_type": "Individual",
            "verification_status": "Verified",
            "email_verified": 1,
        }

        with (
            patch.object(main, "fetch_one", return_value=donor_row),
            patch.object(main, "verify_password", return_value=True),
        ):
            status, headers, body = await self.call(
                "POST",
                "/donors/login",
                {"username": "donor@example.com", "password": "correct"},
                client_ip="198.51.100.21",
                form=True,
            )

        self.assertEqual(status, 200, body)
        self.assertTrue(body["access_token"])
        self.assertEqual(body["donor"]["email"], "donor@example.com")
        self.assertIn("x-ratelimit-limit", headers)
        self.assertIn("x-ratelimit-remaining", headers)

    async def test_admin_login_success_returns_200_with_rate_limit_headers(self):
        with (
            patch.object(main, "ADMIN_USERNAME", "test-admin"),
            patch.object(main, "ADMIN_PASSWORD_HASH", "configured-hash"),
            patch.object(
                main,
                "ADMIN_TOKEN_SECRET",
                "test-secret-for-contract-tests-only-32b",
            ),
            patch.object(main, "verify_password", return_value=True),
        ):
            status, headers, body = await self.call(
                "POST",
                "/admin/login",
                {"username": "test-admin", "password": "correct"},
                client_ip="198.51.100.22",
            )

        self.assertEqual(status, 200, body)
        self.assertTrue(body["access_token"])
        self.assertIn("x-ratelimit-limit", headers)

    async def test_second_successful_login_is_still_200(self):
        """Repeated successful logins must not trip the limiter's header path."""

        donor_row = {
            "id": 8,
            "email": "donor@example.com",
            "password_hash": "stored-hash",
            "full_name": "Test Donor",
            "account_type": "Individual",
            "verification_status": "Verified",
            "email_verified": 1,
        }

        with (
            patch.object(main, "fetch_one", return_value=donor_row),
            patch.object(main, "verify_password", return_value=True),
        ):
            for _ in range(3):
                status, headers, body = await self.call(
                    "POST",
                    "/donors/login",
                    {"username": "donor@example.com", "password": "correct"},
                    client_ip="198.51.100.23",
                    form=True,
                )

                self.assertEqual(status, 200, body)

            self.assertEqual(headers["x-ratelimit-remaining"], "2")

    # --------------------------------------------------
    # Donation edits
    # --------------------------------------------------

    async def test_edit_with_explicit_null_does_not_write_to_not_null_column(self):
        self.use_donor({"id": 7, "full_name": "Test Donor", "phone": "9800000000"})

        with (
            patch.object(
                main,
                "fetch_one",
                return_value={"id": 42, "status": "Available"},
            ),
            patch.object(
                main,
                "execute_query",
                side_effect=AssertionError("no UPDATE should be issued"),
            ),
        ):
            status, _, body = await self.call(
                "PUT",
                "/donors/me/donations/42",
                {"food_name": None, "quantity": None},
            )

        self.assertEqual(status, 400, body)
        self.assertEqual(body["detail"], "No fields to update.")

    async def test_edit_rejects_text_that_is_only_whitespace(self):
        self.use_donor({"id": 7, "full_name": "Test Donor", "phone": "9800000000"})

        with (
            patch.object(
                main,
                "fetch_one",
                return_value={"id": 42, "status": "Available"},
            ),
            patch.object(
                main,
                "execute_query",
                side_effect=AssertionError("no UPDATE should be issued"),
            ),
        ):
            status, _, body = await self.call(
                "PUT",
                "/donors/me/donations/42",
                {"food_name": "   "},
            )

        self.assertEqual(status, 422, body)
        self.assertIn("Food name cannot be empty", body["detail"])

    async def test_edit_rejects_expiry_date_in_the_past(self):
        self.use_donor({"id": 7, "full_name": "Test Donor", "phone": "9800000000"})

        with (
            patch.object(
                main,
                "fetch_one",
                return_value={"id": 42, "status": "Available"},
            ),
            patch.object(
                main,
                "execute_query",
                side_effect=AssertionError("no UPDATE should be issued"),
            ),
        ):
            status, _, body = await self.call(
                "PUT",
                "/donors/me/donations/42",
                {"expiry_date": "2020-01-01T00:00:00"},
            )

        self.assertEqual(status, 422, body)
        self.assertIn("Expiry date cannot be in the past", body["detail"])

    async def test_edit_cannot_change_server_owned_status(self):
        self.use_donor({"id": 7, "full_name": "Test Donor", "phone": "9800000000"})

        with (
            patch.object(
                main,
                "fetch_one",
                return_value={"id": 42, "status": "Available"},
            ),
            patch.object(
                main,
                "execute_query",
                side_effect=AssertionError("no execute_query call expected"),
            ),
        ):
            status, _, body = await self.call(
                "PUT",
                "/donors/me/donations/1",
                {"status": "Completed"},
            )

        self.assertEqual(status, 400, body)

    # --------------------------------------------------
    # Donation creation
    # --------------------------------------------------

    async def test_create_donation_uses_the_posted_contact_details(self):
        self.use_donor({"id": 7, "full_name": "Account Name", "phone": "9800000000"})

        captured = {}

        def fake_execute(sql, params=()):
            captured["sql"] = sql
            captured["params"] = params
            return 123, 1

        deadline = (datetime.now() + timedelta(days=2)).isoformat()

        with patch.object(main, "execute_query", side_effect=fake_execute):
            status, _, body = await self.call(
                "POST",
                "/donations",
                {
                    "food_name": "Rice bags",
                    "item_type": "Food",
                    "quantity": 5,
                    "category": "Grains",
                    "location": "Kathmandu",
                    "pickup_deadline": deadline,
                    "donor_name": "Volunteer Desk",
                    "contact": "9812345678",
                },
            )

        self.assertEqual(status, 201, body)
        self.assertEqual(body["donation_id"], 123)
        self.assertIn("Volunteer Desk", captured["params"])
        self.assertIn("9812345678", captured["params"])
        self.assertNotIn("Account Name", captured["params"])

    async def test_create_donation_falls_back_to_the_account_contact(self):
        self.use_donor({"id": 7, "full_name": "Account Name", "phone": "9800000000"})

        captured = {}

        def fake_execute(sql, params=()):
            captured["params"] = params
            return 124, 1

        deadline = (datetime.now() + timedelta(days=2)).isoformat()

        with patch.object(main, "execute_query", side_effect=fake_execute):
            status, _, body = await self.call(
                "POST",
                "/donations",
                {
                    "food_name": "Rice bags",
                    "item_type": "Food",
                    "quantity": 5,
                    "category": "Grains",
                    "location": "Kathmandu",
                    "pickup_deadline": deadline,
                },
            )

        self.assertEqual(status, 201, body)
        self.assertIn("Account Name", captured["params"])
        self.assertIn("9800000000", captured["params"])

    async def test_create_donation_rejects_a_deadline_in_the_past(self):
        self.use_donor({"id": 7, "full_name": "Account Name", "phone": "9800000000"})

        with patch.object(
            main,
            "execute_query",
            side_effect=AssertionError("no INSERT should be issued"),
        ):
            status, _, body = await self.call(
                "POST",
                "/donations",
                {
                    "food_name": "Rice bags",
                    "item_type": "Food",
                    "quantity": 5,
                    "category": "Grains",
                    "location": "Kathmandu",
                    "pickup_deadline": "2020-01-01T00:00:00",
                },
            )

        self.assertEqual(status, 422, body)
        self.assertIn("Pickup deadline must be in the future", body["detail"])

    async def test_create_donation_rejects_an_unknown_item_type(self):
        """donations.item_type only stores Food/Drink; others must be a 422."""

        self.use_donor({"id": 7, "full_name": "Account Name", "phone": "9800000000"})

        deadline = (datetime.now() + timedelta(days=2)).isoformat()

        with patch.object(
            main,
            "execute_query",
            side_effect=AssertionError("no INSERT should be issued"),
        ):
            status, _, body = await self.call(
                "POST",
                "/donations",
                {
                    "food_name": "Mystery box",
                    "item_type": "Cooked food",
                    "quantity": 1,
                    "category": "Other",
                    "location": "Kathmandu",
                    "pickup_deadline": deadline,
                },
            )

        self.assertEqual(status, 422, body)

    # --------------------------------------------------
    # Password recovery must not reveal accounts
    # --------------------------------------------------

    async def test_forgot_password_cools_down_unknown_addresses_too(self):
        """Unknown and registered addresses must answer the same 429."""

        payload = {"email": "someone@example.com"}

        common = (
            patch.object(main, "validate_email_domain", return_value=True),
            patch.object(main, "purge_stale_otp_data", return_value=None),
            patch.object(main, "delivery_mode", return_value="smtp"),
            patch.object(main, "seconds_until_resend_allowed", return_value=42),
        )

        with common[0], common[1], common[2], common[3]:
            with patch.object(main, "fetch_one", return_value=None):
                status_unknown, _, body_unknown = await self.call(
                    "POST",
                    "/donors/password/forgot",
                    payload,
                    client_ip="198.51.100.31",
                )

            main.limiter.reset()

            with patch.object(
                main,
                "fetch_one",
                return_value={"id": 3, "full_name": "Known Donor"},
            ):
                status_known, _, body_known = await self.call(
                    "POST",
                    "/donors/password/forgot",
                    payload,
                    client_ip="198.51.100.32",
                )

        self.assertEqual(status_unknown, 429, body_unknown)
        self.assertEqual(status_known, 429, body_known)
        self.assertEqual(body_unknown["detail"], body_known["detail"])

    # --------------------------------------------------
    # Read paths
    # --------------------------------------------------

    async def test_missing_donation_returns_404(self):
        with patch.object(main, "fetch_one", return_value=None):
            status, _, body = await self.call("GET", "/donations/999999")

        self.assertEqual(status, 404, body)

    # --------------------------------------------------
    # Donation detail must not leak contact numbers
    # --------------------------------------------------

    async def test_donation_detail_publishes_contact_while_collectable(self):
        row = {
            "id": 5,
            "status": "Available",
            "contact": "9800000000",
            "donor_name": "Sita Rai",
        }

        with patch.object(main, "fetch_one", return_value=dict(row)):
            status, _, body = await self.call(
                "GET", "/donations/5", client_ip="198.51.100.42"
            )

        self.assertEqual(status, 200, body)
        self.assertTrue(body["contact_visible"])
        self.assertEqual(body["donation"]["contact"], "9800000000")
        self.assertEqual(body["donation"]["donor_name"], "Sita Rai")

    async def test_donation_detail_hides_contact_once_closed(self):
        for closed_status in ("Completed", "Cancelled", "Expired"):
            with self.subTest(status=closed_status):
                main.limiter.reset()

                row = {
                    "id": 5,
                    "status": closed_status,
                    "contact": "9800000000",
                    "donor_name": "Sita Rai",
                }

                with patch.object(main, "fetch_one", return_value=dict(row)):
                    status, _, body = await self.call(
                        "GET", "/donations/5", client_ip="198.51.100.43"
                    )

                self.assertEqual(status, 200, body)
                self.assertFalse(body["contact_visible"])
                self.assertIsNone(body["donation"]["contact"])
                # The rest of the record stays visible so the page still works.
                self.assertEqual(body["donation"]["donor_name"], "Sita Rai")
                self.assertEqual(body["donation"]["status"], closed_status)

    async def test_donation_detail_is_rate_limited(self):
        row = {"id": 5, "status": "Available", "contact": "9800000000"}

        with patch.object(main, "fetch_one", return_value=dict(row)):
            for _ in range(60):
                status, _, _ = await self.call(
                    "GET", "/donations/5", client_ip="198.51.100.44"
                )
                self.assertEqual(status, 200)

            status, _, body = await self.call(
                "GET", "/donations/5", client_ip="198.51.100.44"
            )

        self.assertEqual(status, 429, body)

    async def test_health_reports_database_failure_as_503(self):
        with patch.object(
            main,
            "fetch_one",
            side_effect=RuntimeError("MySQL connection failed"),
        ):
            status, _, body = await self.call("GET", "/health")

        self.assertEqual(status, 503, body)
        self.assertIn("Database connection failed", body["detail"])

    async def test_unknown_route_returns_404(self):
        status, _, _ = await self.call("GET", "/no-such-endpoint")
        self.assertEqual(status, 404)


class SchemaWideningTests(unittest.TestCase):
    """The schema sync must widen stale VARCHAR columns and leave ENUMs alone."""

    class FakeCursor:
        def __init__(self, column_type):
            self.column_type = column_type
            self.statements = []

        def execute(self, sql, params=()):
            self.statements.append(" ".join(sql.split()))

        def fetchone(self):
            return (self.column_type,)

    def alter_count(self, cursor):
        return len([item for item in cursor.statements if item.startswith("ALTER")])

    def test_widens_a_narrow_varchar(self):
        cursor = self.FakeCursor("varchar(150)")

        changed = database._widen_column(
            cursor, "donations", "food_name", "VARCHAR(200) NOT NULL"
        )

        self.assertEqual(changed, "donations.food_name")
        self.assertEqual(self.alter_count(cursor), 1)

    def test_skips_a_column_that_is_already_wide_enough(self):
        cursor = self.FakeCursor("varchar(200)")

        changed = database._widen_column(
            cursor, "donations", "food_name", "VARCHAR(200) NOT NULL"
        )

        self.assertIsNone(changed)
        self.assertEqual(self.alter_count(cursor), 0)

    def test_never_shrinks_a_column(self):
        cursor = self.FakeCursor("varchar(255)")

        changed = database._widen_column(
            cursor, "donor_accounts", "organization_name", "VARCHAR(200) NULL"
        )

        self.assertIsNone(changed)
        self.assertEqual(self.alter_count(cursor), 0)

    def test_leaves_enum_columns_untouched(self):
        cursor = self.FakeCursor("enum('Food','Drink')")

        changed = database._widen_column(
            cursor, "donations", "item_type", "VARCHAR(100) NOT NULL"
        )

        self.assertIsNone(changed)
        self.assertEqual(self.alter_count(cursor), 0)

    def test_ignores_columns_that_do_not_exist(self):
        cursor = self.FakeCursor(None)

        changed = database._widen_column(
            cursor, "donations", "food_name", "VARCHAR(200) NOT NULL"
        )

        self.assertIsNone(changed)
        self.assertEqual(self.alter_count(cursor), 0)


class IndexSyncTests(unittest.TestCase):
    """Secondary indexes declared in the schema must be created when missing."""

    class FakeCursor:
        def __init__(self, existing):
            self.existing = set(existing)
            self.statements = []
            self._row = (0,)

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            self.statements.append(normalized)

            if normalized.startswith("SELECT COUNT(*)"):
                table, index_name = params
                self._row = (1 if (table, index_name) in self.existing else 0,)

        def fetchone(self):
            return self._row

    def test_creates_a_missing_index(self):
        cursor = self.FakeCursor(existing=[])

        created = database._ensure_index(
            cursor, "donations", "idx_donations_status", "status"
        )

        self.assertEqual(created, "donations.idx_donations_status")
        self.assertIn(
            "ALTER TABLE donations ADD INDEX idx_donations_status (status)",
            cursor.statements,
        )

    def test_skips_an_index_that_already_exists(self):
        cursor = self.FakeCursor(
            existing=[("donations", "idx_donations_status")]
        )

        created = database._ensure_index(
            cursor, "donations", "idx_donations_status", "status"
        )

        self.assertIsNone(created)
        self.assertEqual(
            [item for item in cursor.statements if item.startswith("ALTER")],
            [],
        )

    def test_declared_indexes_cover_the_donations_table(self):
        names = {name for _, name, _ in database.INDEXES}

        self.assertEqual(
            names,
            {
                "idx_donations_donor_id",
                "idx_donations_status",
                "idx_donations_category",
            },
        )


class ConnectionPoolTests(unittest.TestCase):
    """Connections must come from one shared pool, and exhaustion must wait."""

    def setUp(self):
        self.saved_pool = database._pool
        database._pool = None

    def tearDown(self):
        database._pool = self.saved_pool

    def test_pool_is_created_once(self):
        fake_pool = object()

        with patch.object(
            database.pooling, "MySQLConnectionPool", return_value=fake_pool
        ) as constructor:
            first = database.get_pool()
            second = database.get_pool()

        self.assertIs(first, fake_pool)
        self.assertIs(second, fake_pool)
        self.assertEqual(constructor.call_count, 1)

    def test_pool_size_is_clamped(self):
        with patch.dict(os.environ, {"DB_POOL_SIZE": "0"}):
            self.assertEqual(database.get_pool_size(), 1)

        with patch.dict(
            os.environ, {"DB_POOL_SIZE": str(database.pooling.CNX_POOL_MAXSIZE + 5)}
        ):
            self.assertEqual(
                database.get_pool_size(), database.pooling.CNX_POOL_MAXSIZE
            )

        with patch.dict(os.environ, {"DB_POOL_SIZE": "not-a-number"}):
            self.assertEqual(database.get_pool_size(), database.DEFAULT_POOL_SIZE)

    def test_borrow_returns_a_pooled_connection(self):
        connection = object()
        pool = MagicMock()
        pool.get_connection.return_value = connection

        with patch.object(database, "get_pool", return_value=pool):
            self.assertIs(database.get_connection(), connection)

    def test_exhausted_pool_waits_then_reports_busy(self):
        pool = MagicMock()
        pool.get_connection.side_effect = database.pooling.PoolError("exhausted")

        with (
            patch.object(database, "get_pool", return_value=pool),
            patch.object(database, "POOL_WAIT_SECONDS", 0.05),
        ):
            started = time.monotonic()

            with self.assertRaises(RuntimeError):
                database.get_connection()

            waited = time.monotonic() - started

        self.assertGreaterEqual(waited, 0.05)
        self.assertGreater(pool.get_connection.call_count, 1)


if __name__ == "__main__":
    unittest.main()

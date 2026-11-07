import unittest
from unittest.mock import patch

from fastapi import HTTPException

from main import is_single_character_typo, validate_email_domain


class EmailDomainValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

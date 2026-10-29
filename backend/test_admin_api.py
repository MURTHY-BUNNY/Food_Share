"""Smoke test for the FoodShare admin API.

Usage:
    python test_admin_api.py

The admin login endpoint expects a JSON body, so credentials are sent as JSON
and the returned bearer token is reused for the protected endpoints.
"""

import getpass
import sys

import requests

BASE_URL = "http://127.0.0.1:8000"


def main() -> int:
    username = input("Admin username: ")
    password = getpass.getpass("Admin password: ")

    try:
        login = requests.post(
            f"{BASE_URL}/admin/login",
            json={"username": username, "password": password},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"Could not reach {BASE_URL}: {exc}")
        return 1

    print("Login status:", login.status_code)

    if login.status_code != 200:
        print("Login failed:", login.text)
        return 1

    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    endpoints = [
        ("/admin/donations", "donations"),
        ("/admin/donors", "donors"),
        ("/food-requests", "requests"),
    ]

    failed = False

    for path, key in endpoints:
        try:
            response = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=15)
        except requests.RequestException as exc:
            print(f"{path}: request failed ({exc})")
            failed = True
            continue

        if response.status_code != 200:
            print(f"{path}: HTTP {response.status_code} - {response.text}")
            failed = True
            continue

        records = response.json().get(key, [])
        print(f"{path}: {len(records)} record(s)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

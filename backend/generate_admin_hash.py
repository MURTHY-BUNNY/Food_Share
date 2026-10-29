
import hashlib
import secrets
from getpass import getpass

password = getpass("Enter the new admin password: ")

if len(password) < 12:
    raise SystemExit("Use a password with at least 12 characters.")

salt = secrets.token_hex(16)

derived = hashlib.pbkdf2_hmac(
    "sha256",
    password.encode("utf-8"),
    salt.encode("utf-8"),
    600_000,
)

print("\nAdd this as ADMIN_PASSWORD_HASH in the project .env file")
print("(the same file that holds DB_PASSWORD and ADMIN_TOKEN_SECRET):")
print(f"pbkdf2_sha256${salt}${derived.hex()}")

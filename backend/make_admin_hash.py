
import getpass
import hashlib
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)

    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        600_000,
    )

    return f"pbkdf2_sha256${salt}${derived_key.hex()}"


def main():
    password = getpass.getpass("Enter new admin password: ")
    confirm_password = getpass.getpass("Confirm admin password: ")

    if len(password) < 8:
        print("Password must contain at least 8 characters.")
        return

    if password != confirm_password:
        print("Passwords do not match.")
        return

    print("\nCopy this value into ADMIN_PASSWORD_HASH in the project .env file:")
    print(hash_password(password))
    print("\nKeep your password and hash private.")


if __name__ == "__main__":
    main()

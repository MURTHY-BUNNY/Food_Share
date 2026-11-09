
import os
import hashlib
import hmac
import secrets
import dns.resolver
import dns.exception
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from pathlib import Path
import jwt
import mysql.connector
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    File,
)
from fastapi.staticfiles import StaticFiles
import uuid
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from fastapi.security import (
    OAuth2PasswordRequestForm,
    HTTPBearer,
    HTTPAuthorizationCredentials,
)
from pydantic import BaseModel, Field, EmailStr
from mysql.connector import Error

from database import get_connection, initialize_database
from emailer import (
    EmailDeliveryError,
    delivery_mode,
    send_otp,
    send_test,
    send_welcome_email,
    status as mail_status,
)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

LOGIN_RATE_LIMIT = "5/minute"

# The public donation page is reachable without a session, so cap how fast ids
# can be walked. A browser opening a listing uses a handful of requests.
DETAIL_RATE_LIMIT = "60/minute"

# Donation statuses whose donor contact number may be published.
CONTACT_VISIBLE_STATUSES = ("Available", "Reserved")
limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=True,
)

# --------------------------------------------------
# DONOR PROFILE PHOTO STORAGE
# --------------------------------------------------

UPLOAD_DIR = BASE_DIR / "uploads" / "donor-photos"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    # Some browsers still send the non-standard aliases for JPEG.
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_PHOTO_SIZE = 2 * 1024 * 1024  # 2 MB


def photo_file(relative_path):
    """Resolve a stored photo path, refusing anything outside the upload folder."""
    if not relative_path:
        return None

    candidate = (BASE_DIR / relative_path.lstrip("/")).resolve()

    if candidate.parent != UPLOAD_DIR.resolve():
        return None

    return candidate


def delete_photo_file(relative_path) -> None:
    """Delete a stored photo from disk, ignoring missing or unsafe paths."""
    target = photo_file(relative_path)

    if not target or not target.is_file():
        return

    try:
        target.unlink()
    except OSError as exc:
        print(f"Could not remove profile photo: {type(exc).__name__}")


# --------------------------------------------------
# EMAIL DOMAIN VALIDATION
# --------------------------------------------------

DNS_TIMEOUT_SECONDS = 3

DEFAULT_DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "tempmail.com",
    "10minutemail.com",
    "guerrillamail.com",
    "yopmail.com",
    "throwawaymail.com",
}

COMMON_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "protonmail.com",
    "proton.me",
    "rediffmail.com",
    "aol.com",
}


def is_single_character_typo(value: str, candidate: str) -> bool:
    if abs(len(value) - len(candidate)) > 1:
        return False

    if len(value) == len(candidate):
        mismatches = [
            index
            for index, (left, right) in enumerate(zip(value, candidate))
            if left != right
        ]
        if len(mismatches) == 1:
            return True
        if len(mismatches) == 2:
            first, second = mismatches
            return (
                second == first + 1
                and value[first] == candidate[second]
                and value[second] == candidate[first]
            )
        return False

    shorter, longer = sorted((value, candidate), key=len)
    short_index = 0
    long_index = 0
    skipped_character = False

    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
        elif skipped_character:
            return False
        else:
            skipped_character = True
            long_index += 1

    return True


def validate_email_domain(email: str) -> bool:
    email = email.strip().lower()
    domain = email.rsplit("@", 1)[-1]

    suggestions = sorted(
        (
            candidate
            for candidate in COMMON_EMAIL_DOMAINS
            if candidate != domain
            and is_single_character_typo(domain, candidate)
        ),
        key=len,
    )
    if suggestions:
        local_part = email.rsplit("@", 1)[0]
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid email address. Did you mean "
                f"{local_part}@{suggestions[0]}? Correct it before requesting a verification code."
            ),
        )

    configured_domains = {
        item.strip().lower()
        for item in os.getenv(
            "DISPOSABLE_EMAIL_DOMAINS", ""
        ).split(",")
        if item.strip()
    }

    blocked_domains = (
        DEFAULT_DISPOSABLE_DOMAINS | configured_domains
    )

    if domain in blocked_domains:
        raise HTTPException(
            status_code=422,
            detail="Disposable email addresses are not allowed."
        )

    resolver = dns.resolver.Resolver()
    resolver.timeout = DNS_TIMEOUT_SECONDS
    resolver.lifetime = DNS_TIMEOUT_SECONDS

    try:
        answers = resolver.resolve(domain, "MX")

        if len(answers) > 0:
            return True

    except dns.resolver.NoAnswer:
        # Try implicit MX fallback below.
        pass

    except dns.resolver.NXDOMAIN:
        raise HTTPException(
            status_code=422,
            detail="Wrong email ID entered. Please check the domain."
        )

    except (
        dns.exception.Timeout,
        dns.resolver.NoNameservers
    ):
        raise HTTPException(
            status_code=503,
            detail="Email domain verification is temporarily unavailable. Please try again."
        )

    # RFC implicit MX fallback: accept a domain with A or AAAA.
    for record_type in ("A", "AAAA"):
        try:
            answers = resolver.resolve(domain, record_type)

            if len(answers) > 0:
                return True

        except (
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN
        ):
            continue

        except (
            dns.exception.Timeout,
            dns.resolver.NoNameservers
        ):
            raise HTTPException(
                status_code=503,
                detail="Email domain verification is temporarily unavailable. Please try again."
            )

    raise HTTPException(
        status_code=422,
        detail="Wrong email ID entered. This domain cannot receive email."
    )


# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Create the database schema before serving requests.

    A database outage must not take the whole API down: the failure is
    logged and surfaced by /health instead of crashing the process.
    """
    try:
        initialize_database()
    except Exception as exc:  # noqa: BLE001 - startup must not raise
        print(f"WARNING: database initialization failed: {exc}")

    yield


app = FastAPI(
    title="FoodShare API",
    description="Food donation and food access management API",
    version="1.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount(
    "/uploads",
    StaticFiles(directory=str(BASE_DIR / "uploads")),
    name="uploads",
)

# The static frontend is served by Live Server / http.server, which can pick
# any local port, and may also be opened straight from disk (origin "null").
configured_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
DONOR_TOKEN_SECRET = os.getenv("DONOR_TOKEN_SECRET", "")
ADMIN_TOKEN_SECRET = os.getenv("ADMIN_TOKEN_SECRET", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_ONBOARDING_TTL_MINUTES = 10

if not DONOR_TOKEN_SECRET or not ADMIN_TOKEN_SECRET:
    print(
        "WARNING: Set DONOR_TOKEN_SECRET and ADMIN_TOKEN_SECRET "
        "in backend/.env before using authentication."
    )


# --------------------------------------------------
# EMAIL CODE CONFIGURATION
# --------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


# Codes are stored as a keyed digest, so a database dump cannot be turned back
# into working codes without this secret.
OTP_SECRET = os.getenv("OTP_SECRET", "") or DONOR_TOKEN_SECRET
ALLOW_DEMO_EMAIL = os.getenv("ALLOW_DEMO_EMAIL", "").strip().lower() in {"1", "true", "yes", "on"}

SIGNUP_CODE_TTL_MINUTES = _env_int("SIGNUP_CODE_TTL_MINUTES", 15)
RESET_CODE_TTL_MINUTES = _env_int("RESET_CODE_TTL_MINUTES", 15)
RESET_TOKEN_TTL_MINUTES = 15
OTP_RESEND_SECONDS = _env_int("OTP_RESEND_SECONDS", 60)
OTP_MAX_ATTEMPTS = _env_int("OTP_MAX_ATTEMPTS", 5)
PENDING_REGISTRATION_TTL_HOURS = 24

_mail = mail_status()

if _mail["mode"] == "smtp":
    print(
        f"Email delivery: sending through {_mail['host']}:{_mail['port']} "
        f"({_mail['transport']}) as {_mail['sender'] or 'no sender set'}."
    )
elif _mail["partial"]:
    print(
        f"WARNING: email delivery is half-configured - {_mail['reason']}. "
        "Codes are shown on the page until this is fixed."
    )
else:
    print(
        "NOTICE: SMTP is not configured, so verification codes are printed "
        "here and returned to the page instead of being emailed. "
        "Add SMTP_HOST, SMTP_USER and SMTP_PASSWORD to backend/.env to send "
        "real mail."
    )


# --------------------------------------------------
# DATABASE HELPERS
# --------------------------------------------------

def fetch_one(sql, params=()):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()
        connection.close()


def fetch_all(sql, params=()):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def execute_query(sql, params=()):
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        connection.commit()
        return cursor.lastrowid, cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def deliver_welcome_email(
    donor_id,
    email,
    full_name,
    account_type,
    verification_status,
):
    """Create one tracking record and attempt a new donor's welcome email.

    This function deliberately catches its own errors so email or tracking
    failures cannot undo an already committed donor account.
    """
    try:
        # INSERT IGNORE returns rowcount=1 only for a newly inserted record;
        # an existing donor record is left untouched and is not emailed again.
        _, inserted = execute_query(
            """
            INSERT IGNORE INTO donor_welcome_emails
                (donor_id, email, status)
            VALUES (%s, %s, 'Pending')
            """,
            (donor_id, email),
        )

        if inserted != 1:
            print(
                f"Welcome email skipped for donor ID {donor_id}: "
                "a tracking record already exists."
            )
            return

        # Count this attempt before contacting the SMTP server.
        execute_query(
            """
            UPDATE donor_welcome_emails
            SET attempts = attempts + 1, last_error = NULL
            WHERE donor_id = %s
            """,
            (donor_id,),
        )

        try:
            delivery_result = send_welcome_email(
                to_email=email,
                recipient_name=full_name,
                account_type=account_type,
                verification_status=verification_status,
            )

            if delivery_result != "smtp":
                # Demo mode prints OTPs for development; it is not email delivery.
                execute_query(
                    """
                    UPDATE donor_welcome_emails
                    SET status = 'Failed', last_error = %s
                    WHERE donor_id = %s
                    """,
                    ("SMTPNotConfigured", donor_id),
                )
                print(
                    f"Welcome email not sent for donor ID {donor_id}: "
                    "SMTP is not configured."
                )
                return

            execute_query(
                """
                UPDATE donor_welcome_emails
                SET status = 'Sent', sent_at = NOW(), last_error = NULL
                WHERE donor_id = %s
                """,
                (donor_id,),
            )
            print(f"Welcome email accepted by SMTP for donor ID {donor_id}.")

        except Exception as exc:
            error_name = type(exc).__name__[:500]
            try:
                execute_query(
                    """
                    UPDATE donor_welcome_emails
                    SET status = 'Failed', last_error = %s
                    WHERE donor_id = %s
                    """,
                    (error_name, donor_id),
                )
            except Exception as tracking_exc:
                print(
                    f"WARNING: Could not update welcome-email status for "
                    f"donor ID {donor_id}: {type(tracking_exc).__name__}"
                )

            print(
                f"WARNING: Welcome email failed for donor ID {donor_id}: "
                f"{error_name}"
            )

    except Exception as exc:
        print(
            f"WARNING: Welcome-email tracking failed for donor ID "
            f"{donor_id}: {type(exc).__name__}"
        )


def expire_stale_donations():
    """Retire donations that can no longer be collected.

    A donation stops being collectable once its pickup deadline passes or its
    expiry date is in the past. Keeping the stored status in sync means the
    public listings, the donor dashboard and the admin console all agree.
    """
    try:
        execute_query(
            """
            UPDATE donations
            SET status = 'Expired'
            WHERE status IN ('Available', 'Reserved')
              AND (
                    pickup_deadline < NOW()
                    OR (expiry_date IS NOT NULL AND expiry_date < CURDATE())
                  )
            """
        )
    except Exception as exc:  # noqa: BLE001 - never block a read request
        print(f"WARNING: could not expire stale donations: {exc}")


# --------------------------------------------------
# DATETIME HELPERS
# --------------------------------------------------

def to_server_local(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise an incoming datetime to naive server-local time.

    Browsers send pickup and expiry timestamps as ISO-8601 in UTC
    ("2026-10-29T12:30:00Z") while MySQL's NOW() and CURDATE() run in the
    server's local zone. Dropping the offset with replace(tzinfo=None) would
    shift every deadline by the machine's UTC offset, so convert it first.
    """
    if value is None:
        return None

    if value.tzinfo is None:
        return value

    return value.astimezone().replace(tzinfo=None)


# --------------------------------------------------
# PASSWORD HELPERS
# --------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256."""
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        600_000,
    )
    return f"pbkdf2_sha256${salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a PBKDF2 hash."""
    if not stored_hash:
        return False

    try:
        algorithm, salt, expected = stored_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            600_000,
        ).hex()

        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# --------------------------------------------------
# TOKEN HELPERS
# --------------------------------------------------

def create_token(subject: str, secret: str, token_type: str) -> str:
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Authentication secret is not configured.",
        )

    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(hours=12),
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str, expected_type: str):
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Authentication secret is not configured.",
        )

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token.")

    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type.")

    return payload



# Swagger UI Bearer authentication
bearer_scheme = HTTPBearer(auto_error=False)


def get_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        bearer_scheme
    )
):
    """Read and return the Bearer JWT token."""

    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=401,
            detail="Authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials.strip()


def get_current_donor(token: str = Depends(get_bearer_token)):
    payload = decode_token(token, DONOR_TOKEN_SECRET, "donor")

    try:
        donor_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid donor token.")

    donor = fetch_one(
        """
        SELECT id, full_name, account_type, organization_name,
               email, phone, location, verification_status,
               email_verified, email_verified_at, password_changed_at,
               created_at, profile_photo_path
        FROM donor_accounts
        WHERE id = %s
        """,
        (donor_id,),
    )

    if not donor:
        raise HTTPException(status_code=401, detail="Donor account not found.")

    # Resetting a password signs out browsers that still hold the old one.
    issued_at = payload.get("iat")
    changed_at = donor.get("password_changed_at")

    if changed_at and isinstance(issued_at, (int, float)):
        try:
            still_valid = datetime.fromtimestamp(issued_at) >= changed_at
        except (OverflowError, OSError, ValueError):
            still_valid = True

        if not still_valid:
            raise HTTPException(
                status_code=401,
                detail="Your password was changed. Sign in again.",
            )

    donor.pop("password_changed_at", None)

    return donor


def get_current_admin(token: str = Depends(get_bearer_token)):
    payload = decode_token(token, ADMIN_TOKEN_SECRET, "admin")
    if payload.get("sub") != ADMIN_USERNAME:
        raise HTTPException(status_code=401, detail="Invalid admin token.")
    return payload


# --------------------------------------------------
# ONE-TIME CODE HELPERS
# --------------------------------------------------

def otp_digest(email: str, purpose: str, code: str) -> str:
    """Keyed digest of a code, so stored codes cannot be reversed."""
    message = f"{purpose}:{email}:{code}".encode("utf-8")

    return hmac.new(OTP_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def purge_stale_otp_data():
    """Drop codes and sign-ups that can no longer be used."""
    try:
        execute_query(
            "DELETE FROM email_otps WHERE expires_at < NOW() - INTERVAL 1 DAY"
        )
        execute_query(
            "DELETE FROM pending_registrations "
            "WHERE created_at < NOW() - INTERVAL %s HOUR",
            (PENDING_REGISTRATION_TTL_HOURS,),
        )
    except Exception as exc:  # noqa: BLE001 - housekeeping must not block
        print(f"WARNING: could not clear stale verification data: {exc}")


def seconds_until_resend_allowed(email: str, purpose: str) -> int:
    """Seconds left before another code may be requested for this address."""
    row = fetch_one(
        """
        SELECT TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age_seconds
        FROM email_otps
        WHERE email = %s AND purpose = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (email, purpose),
    )

    if not row or row["age_seconds"] is None:
        return 0

    return max(0, OTP_RESEND_SECONDS - int(row["age_seconds"]))


def issue_code(email: str, purpose: str, ttl_minutes: int) -> str:
    """Create a fresh 6-digit code, retiring any earlier one."""
    wait = seconds_until_resend_allowed(email, purpose)

    if wait:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Please wait {wait} more second{'s' if wait != 1 else ''} "
                "before requesting another code."
            ),
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now() + timedelta(minutes=ttl_minutes)

    execute_query(
        """
        UPDATE email_otps
        SET consumed_at = NOW()
        WHERE email = %s AND purpose = %s AND consumed_at IS NULL
        """,
        (email, purpose),
    )

    execute_query(
        """
        INSERT INTO email_otps (email, purpose, code_hash, expires_at)
        VALUES (%s, %s, %s, %s)
        """,
        (email, purpose, otp_digest(email, purpose, code), expires_at),
    )

    return code


def consume_code(email: str, purpose: str, code: str):
    """Validate a code and mark it used. Raises 400 when it is not valid."""
    submitted = str(code or "").strip().replace(" ", "")

    row = fetch_one(
        """
        SELECT id, code_hash, attempts, expires_at
        FROM email_otps
        WHERE email = %s AND purpose = %s AND consumed_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (email, purpose),
    )

    if not row:
        raise HTTPException(
            status_code=400,
            detail="That code is no longer valid. Request a new one.",
        )

    if row["expires_at"] < datetime.now():
        execute_query(
            "UPDATE email_otps SET consumed_at = NOW() WHERE id = %s",
            (row["id"],),
        )
        raise HTTPException(
            status_code=400,
            detail="That code has expired. Request a new one.",
        )

    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        execute_query(
            "UPDATE email_otps SET consumed_at = NOW() WHERE id = %s",
            (row["id"],),
        )
        raise HTTPException(
            status_code=400,
            detail="Too many incorrect attempts. Request a new code.",
        )

    if not hmac.compare_digest(row["code_hash"], otp_digest(email, purpose, submitted)):
        execute_query(
            "UPDATE email_otps SET attempts = attempts + 1 WHERE id = %s",
            (row["id"],),
        )

        remaining = OTP_MAX_ATTEMPTS - (int(row["attempts"]) + 1)

        raise HTTPException(
            status_code=400,
            detail=(
                "That code is not correct. Request a new one."
                if remaining <= 0
                else "That code is not correct. "
                f"{remaining} attempt{'s' if remaining != 1 else ''} left."
            ),
        )

    execute_query(
        "UPDATE email_otps SET consumed_at = NOW() WHERE id = %s",
        (row["id"],),
    )


def send_code_email(
    email: str,
    purpose: str,
    code: str,
    recipient_name: Optional[str],
    ttl_minutes: int,
) -> str:
    """Hand the code to the mailer and return the delivery mode used."""
    try:
        delivery = send_otp(email, code, purpose, recipient_name, ttl_minutes)

        if delivery == "demo" and not ALLOW_DEMO_EMAIL:
            execute_query(
                """
                DELETE FROM email_otps
                WHERE email = %s AND purpose = %s AND consumed_at IS NULL
                """,
                (email, purpose),
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Email delivery is not configured on this server. "
                    "Set SMTP_HOST, SMTP_USER and SMTP_PASSWORD in backend/.env "
                    "before continuing."
                ),
            )

        return delivery
    except EmailDeliveryError:
        # No message went out, so drop the code: the donor can retry at once
        # instead of waiting out the resend cooldown for a code they never saw.
        execute_query(
            """
            DELETE FROM email_otps
            WHERE email = %s AND purpose = %s AND consumed_at IS NULL
            """,
            (email, purpose),
        )
        raise HTTPException(
            status_code=502,
            detail="The verification email could not be sent. Try again in a moment.",
        )


def code_issued_response(
    message: str,
    email: str,
    mode: str,
    code: str,
    ttl_minutes: int,
) -> dict:
    payload = {
        "message": message,
        "verification_required": True,
        "email": email,
        "delivery": mode,
        "expires_in_minutes": ttl_minutes,
        "resend_after_seconds": OTP_RESEND_SECONDS,
    }

    if mode == "demo" and ALLOW_DEMO_EMAIL:
        # Only expose demo codes in explicitly enabled local development.
        payload["dev_code"] = code

    return payload


def create_reset_token(email: str) -> str:
    """Short-lived token that proves the reset code was accepted."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "type": "reset",
        "iat": now,
        "exp": now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    }

    return jwt.encode(payload, DONOR_TOKEN_SECRET, algorithm="HS256")


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class DonorProfileUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    phone: str = Field(min_length=7, max_length=30)
    location: str = Field(min_length=2, max_length=255)
    organization_name: Optional[str] = Field(default=None, max_length=200)


class DonorPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DonorRegister(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    account_type: Literal["Individual", "Organization"] = "Individual"
    organization_name: Optional[str] = Field(default=None, max_length=200)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=30)
    location: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class GoogleDonorStart(BaseModel):
    credential: str = Field(min_length=20, max_length=10000)


class GoogleDonorComplete(BaseModel):
    onboarding_token: str = Field(min_length=20, max_length=5000)
    account_type: Literal["Individual", "Organization"] = "Individual"
    organization_name: Optional[str] = Field(default=None, max_length=200)
    phone: str = Field(min_length=7, max_length=30)
    location: str = Field(min_length=2, max_length=255)


DonationItemType = Literal["Food", "Drink"]


class DonationCreate(BaseModel):
    food_name: str = Field(min_length=2, max_length=200)
    # Kept to the two values the donations.item_type column accepts. Anything
    # else used to reach the database and come back as a 500.
    item_type: DonationItemType
    quantity: float = Field(gt=0, le=1000000)
    quantity_unit: str = Field(default="items", max_length=30)
    category: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=2, max_length=255)
    pickup_deadline: datetime
    # Optional: when omitted the donor's own name and phone number from the
    # authenticated account are used, so a donor never has to retype them.
    donor_name: Optional[str] = Field(default=None, max_length=150)
    contact: Optional[str] = Field(default=None, max_length=30)
    expiry_date: Optional[datetime] = None
    packaging_condition: Optional[str] = Field(default=None, max_length=100)


class DonationUpdate(BaseModel):
    food_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    item_type: Optional[DonationItemType] = None
    quantity: Optional[float] = Field(default=None, gt=0, le=1000000)
    quantity_unit: Optional[str] = Field(default=None, max_length=30)
    category: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=255)
    pickup_deadline: Optional[datetime] = None
    donor_name: Optional[str] = Field(default=None, max_length=150)
    contact: Optional[str] = Field(default=None, max_length=30)
    expiry_date: Optional[datetime] = None
    packaging_condition: Optional[str] = Field(default=None, max_length=100)


# Columns a donor may edit on their own donation. Everything else in the
# donations table is server-owned (status, donor_id, timestamps).
EDITABLE_DONATION_FIELDS = {
    "food_name",
    "item_type",
    "quantity",
    "quantity_unit",
    "category",
    "location",
    "pickup_deadline",
    "donor_name",
    "contact",
    "expiry_date",
    "packaging_condition",
}

# Only these columns accept NULL. For every other editable column an explicit
# null in the request body means "leave it as it is" rather than "clear it",
# because writing NULL into a NOT NULL column fails with a 500.
NULLABLE_DONATION_FIELDS = {"expiry_date", "packaging_condition"}

# Text columns that must still hold something after trimming.
REQUIRED_DONATION_TEXT_FIELDS = {
    "food_name",
    "item_type",
    "quantity_unit",
    "category",
    "location",
    "donor_name",
    "contact",
}


class EmailVerification(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class EmailRequest(BaseModel):
    email: EmailStr


class PasswordReset(BaseModel):
    reset_token: str = Field(min_length=10)
    new_password: str = Field(min_length=8, max_length=128)


class MailTestRequest(BaseModel):
    email: EmailStr


class AdminLogin(BaseModel):
    username: str
    password: str


class OrganizationVerification(BaseModel):
    verification_status: Literal["Pending", "Verified", "Rejected"]


class DonationStatusUpdate(BaseModel):
    status: Literal["Available", "Reserved", "Completed", "Expired", "Cancelled"]


class FoodRequestCreate(BaseModel):
    requester_name: str = Field(min_length=2, max_length=150)
    contact: str = Field(min_length=7, max_length=30)
    location: str = Field(min_length=2, max_length=255)
    food_category: str = Field(min_length=1, max_length=100)
    quantity_needed: float = Field(gt=0, le=1000000)


class FoodRequestStatusUpdate(BaseModel):
    request_status: Literal["Pending", "Approved", "Fulfilled", "Rejected"]


# --------------------------------------------------
# HEALTH
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "Welcome to FoodShare API",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    try:
        row = fetch_one("SELECT 1 AS database_ok")
        return {
            "status": "healthy",
            "database": "connected" if row else "unknown",
            # "demo" means codes are shown on the page, not emailed.
            "email": delivery_mode(),
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )


# --------------------------------------------------
# DONOR REGISTRATION AND LOGIN
# --------------------------------------------------

@app.post("/donors/register")
def register_donor(data: DonorRegister):
    """Hold the sign-up and email a 6-digit code.

    The donor account is created by /donors/register/verify once that code is
    entered. An abandoned sign-up therefore never leaves a usable account, and
    an unverified address never occupies an email with someone else's password.
    """
    email = str(data.email).strip().lower()
    validate_email_domain(email)

    existing = fetch_one(
        "SELECT id FROM donor_accounts WHERE email = %s",
        (email,),
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    if (
        data.account_type == "Organization"
        and not (data.organization_name or "").strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="Organization name is required for organization accounts.",
        )

    purge_stale_otp_data()

    execute_query(
        """
        INSERT INTO pending_registrations
            (email, full_name, account_type, organization_name,
             phone, location, password_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            full_name = VALUES(full_name),
            account_type = VALUES(account_type),
            organization_name = VALUES(organization_name),
            phone = VALUES(phone),
            location = VALUES(location),
            password_hash = VALUES(password_hash)
        """,
        (
            email,
            data.full_name.strip(),
            data.account_type,
            (data.organization_name or "").strip() or None,
            data.phone.strip(),
            data.location.strip(),
            hash_password(data.password),
        ),
    )

    code = issue_code(email, "signup", SIGNUP_CODE_TTL_MINUTES)
    mode = send_code_email(
        email,
        "signup",
        code,
        data.full_name.strip(),
        SIGNUP_CODE_TTL_MINUTES,
    )

    return code_issued_response(
        f"We emailed a 6-digit code to {email}. Enter it to finish creating your account.",
        email,
        mode,
        code,
        SIGNUP_CODE_TTL_MINUTES,
    )


@app.post("/donors/register/verify", status_code=201)
def verify_donor_email(data: EmailVerification):
    """Create the account once the emailed code matches, then sign the donor in."""
    email = str(data.email).strip().lower()

    pending = fetch_one(
        "SELECT * FROM pending_registrations WHERE email = %s",
        (email,),
    )

    if not pending:
        raise HTTPException(
            status_code=404,
            detail=(
                "This sign-up has expired. Please fill in the form again to "
                "get a new code."
            ),
        )

    # Check for an existing account before spending the code, so a duplicate
    # sign-up does not burn a code the donor could still need.
    if fetch_one("SELECT id FROM donor_accounts WHERE email = %s", (email,)):
        execute_query(
            "DELETE FROM pending_registrations WHERE email = %s",
            (email,),
        )
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    consume_code(email, "signup", data.code)

    verification = (
        "Pending" if pending["account_type"] == "Organization" else "Verified"
    )

    try:
        donor_id, _ = execute_query(
            """
            INSERT INTO donor_accounts
                (full_name, account_type, organization_name, email, phone,
                 location, password_hash, verification_status,
                 email_verified, email_verified_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                pending["full_name"],
                pending["account_type"],
                pending["organization_name"],
                email,
                pending["phone"],
                pending["location"],
                pending["password_hash"],
                verification,
                1,
            ),
        )
    except mysql.connector.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    execute_query(
        "DELETE FROM pending_registrations WHERE email = %s",
        (email,),
    )

    deliver_welcome_email(
        donor_id=donor_id,
        email=email,
        full_name=pending["full_name"],
        account_type=pending["account_type"],
        verification_status=verification,
    )

    return {
        "message": (
            "Email verified. An administrator still has to verify this "
            "organization before its donations appear in listings."
            if pending["account_type"] == "Organization"
            else "Email verified. Your account is ready."
        ),
        "access_token": create_token(str(donor_id), DONOR_TOKEN_SECRET, "donor"),
        "token_type": "bearer",
        "expires_in": 43200,
        "donor": {
            "id": donor_id,
            "full_name": pending["full_name"],
            "email": email,
            "phone": pending["phone"],
            "location": pending["location"],
            "account_type": pending["account_type"],
            "verification_status": verification,
            "email_verified": True,
        },
    }


@app.post("/donors/register/resend")
def resend_registration_code(data: EmailRequest):
    """Send a new sign-up code for a pending registration."""
    email = str(data.email).strip().lower()
    validate_email_domain(email)

    pending = fetch_one(
        "SELECT full_name FROM pending_registrations WHERE email = %s",
        (email,),
    )

    if not pending:
        raise HTTPException(
            status_code=404,
            detail="This sign-up has expired. Please fill in the form again.",
        )

    code = issue_code(email, "signup", SIGNUP_CODE_TTL_MINUTES)
    mode = send_code_email(
        email,
        "signup",
        code,
        pending["full_name"],
        SIGNUP_CODE_TTL_MINUTES,
    )

    return code_issued_response(
        f"A new code is on its way to {email}.",
        email,
        mode,
        code,
        SIGNUP_CODE_TTL_MINUTES,
    )


@app.post("/donors/google")
def google_donor_start(data: GoogleDonorStart):
    """Verify a Google ID token and either sign in or begin profile completion."""
    if not DONOR_TOKEN_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Donor authentication secret is not configured.",
        )

    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on the server.",
        )

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        claims = id_token.verify_oauth2_token(
            data.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Google authentication dependency is missing. Install google-auth[requests].",
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Google credential is invalid or expired.")
    except Exception:
        raise HTTPException(status_code=401, detail="Google authentication could not be verified.")

    google_sub = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    full_name = str(claims.get("name") or "").strip()
    email_verified = claims.get("email_verified") is True

    if not google_sub or not email or not email_verified:
        raise HTTPException(
            status_code=401,
            detail="Google must provide a verified email address.",
        )

    donor = fetch_one(
        """SELECT id, full_name, account_type, organization_name, email,
                  phone, location, verification_status, email_verified
           FROM donor_accounts WHERE google_sub = %s""",
        (google_sub,),
    )

    # Returning Google-linked donor: issue the normal FoodShare session.
    if donor:
        return {
            "message": "Google sign-in successful.",
            "profile_required": False,
            "access_token": create_token(str(donor["id"]), DONOR_TOKEN_SECRET, "donor"),
            "token_type": "bearer",
            "expires_in": 43200,
            "donor": donor,
        }

    # Link to an existing account only when that FoodShare email was already
    # verified through the normal OTP flow. This prevents account takeover.
    donor = fetch_one(
        """SELECT id, full_name, account_type, organization_name, email,
                  phone, location, verification_status, email_verified, google_sub
           FROM donor_accounts WHERE email = %s""",
        (email,),
    )

    if donor:
        if not bool(donor.get("email_verified")):
            raise HTTPException(
                status_code=409,
                detail="An account exists with this email but it is not verified. Sign in with email and verify it before linking Google.",
            )
        if donor.get("google_sub") and donor["google_sub"] != google_sub:
            raise HTTPException(
                status_code=409,
                detail="This FoodShare account is already linked to another Google account.",
            )
        execute_query(
            "UPDATE donor_accounts SET google_sub = %s WHERE id = %s",
            (google_sub, donor["id"]),
        )
        return {
            "message": "Google account linked. Sign-in successful.",
            "profile_required": False,
            "access_token": create_token(str(donor["id"]), DONOR_TOKEN_SECRET, "donor"),
            "token_type": "bearer",
            "expires_in": 43200,
            "donor": donor,
        }

    # New Google identity: return a short-lived signed onboarding token. The
    # browser cannot choose or alter the verified Google name/email in it.
    now = datetime.now(timezone.utc)
    onboarding_token = jwt.encode(
        {
            "sub": google_sub,
            "email": email,
            "name": full_name[:150],
            "type": "google_onboarding",
            "iat": now,
            "exp": now + timedelta(minutes=GOOGLE_ONBOARDING_TTL_MINUTES),
        },
        DONOR_TOKEN_SECRET,
        algorithm="HS256",
    )

    return {
        "message": "Google verified. Complete your FoodShare profile to continue.",
        "profile_required": True,
        "onboarding_token": onboarding_token,
        "profile": {"full_name": full_name[:150], "email": email},
        "expires_in_minutes": GOOGLE_ONBOARDING_TTL_MINUTES,
    }


@app.post("/donors/google/complete", status_code=201)
def google_donor_complete(data: GoogleDonorComplete):
    """Create a new donor account after Google identity verification."""
    try:
        payload = jwt.decode(
            data.onboarding_token,
            DONOR_TOKEN_SECRET,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Google registration expired. Please start again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid Google registration session.")

    if payload.get("type") != "google_onboarding":
        raise HTTPException(status_code=401, detail="Invalid Google registration session.")

    google_sub = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    full_name = str(payload.get("name") or "").strip() or email.split("@", 1)[0]
    phone = data.phone.strip()
    location = data.location.strip()
    organization_name = (data.organization_name or "").strip() or None

    if len(phone) < 7:
        raise HTTPException(status_code=422, detail="Enter a valid phone number.")
    if len(location) < 2:
        raise HTTPException(status_code=422, detail="Enter your location.")

    if data.account_type == "Organization" and not organization_name:
        raise HTTPException(status_code=422, detail="Organization name is required.")

    if not google_sub or not email:
        raise HTTPException(status_code=401, detail="Google registration session is incomplete.")

    # A token can only be used to create one account. Check both identity and
    # email again to handle repeated submissions and concurrent requests.
    existing = fetch_one(
        "SELECT id FROM donor_accounts WHERE google_sub = %s OR email = %s LIMIT 1",
        (google_sub, email),
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An account with this Google identity or email already exists. Please sign in.",
        )

    verification = "Pending" if data.account_type == "Organization" else "Verified"
    random_password = secrets.token_urlsafe(48)

    try:
        donor_id, _ = execute_query(
            """INSERT INTO donor_accounts
               (full_name, account_type, organization_name, email, phone,
                location, password_hash, verification_status, email_verified,
                email_verified_at, google_sub)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, NOW(), %s)""",
            (
                full_name,
                data.account_type,
                organization_name,
                email,
                phone,
                location,
                hash_password(random_password),
                verification,
                google_sub,
            ),
        )
    except mysql.connector.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="An account with this email or Google identity already exists. Please sign in.",
        )

    donor = {
        "id": donor_id,
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "location": location,
        "account_type": data.account_type,
        "organization_name": organization_name,
        "verification_status": verification,
        "email_verified": True,
    }

    deliver_welcome_email(
        donor_id=donor_id,
        email=email,
        full_name=full_name,
        account_type=data.account_type,
        verification_status=verification,
    )

    return {
        "message": (
            "FoodShare account created. Organization verification is pending."
            if data.account_type == "Organization"
            else "FoodShare account created successfully."
        ),
        "profile_required": False,
        "access_token": create_token(str(donor_id), DONOR_TOKEN_SECRET, "donor"),
        "token_type": "bearer",
        "expires_in": 43200,
        "donor": donor,
    }


@app.post("/donors/login")
@limiter.limit(LOGIN_RATE_LIMIT)
def login_donor(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
):
    # `response` is required, not unused: the limiter writes X-RateLimit-*
    # headers into it. slowapi raises "parameter `response` must be an instance
    # of starlette.responses.Response" when a rate-limited endpoint returns a
    # plain dict without declaring it. FastAPI merges those headers into the
    # JSON response it builds from the dict returned below.
    # OAuth2PasswordRequestForm expects URL-encoded fields:
    # username=<email>&password=<password>
    email = form.username.strip().lower()

    donor = fetch_one(
        """
        SELECT id, email, password_hash, full_name,
               account_type, verification_status, email_verified
        FROM donor_accounts
        WHERE email = %s
        """,
        (email,),
    )

    if not donor or not verify_password(form.password, donor["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_token(
        str(donor["id"]),
        DONOR_TOKEN_SECRET,
        "donor",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 43200,
        "donor": {
            "id": donor["id"],
            "full_name": donor["full_name"],
            "email": donor["email"],
            "account_type": donor["account_type"],
            "verification_status": donor["verification_status"],
            "email_verified": bool(donor["email_verified"]),
        },
    }


# --------------------------------------------------
# DONOR PASSWORD RECOVERY
# --------------------------------------------------

@app.post("/donors/password/forgot")
def forgot_donor_password(data: EmailRequest):
    """Email a reset code.

    The reply has the same shape whether or not the address is registered, so
    this endpoint cannot be used to discover who has an account.
    """
    email = str(data.email).strip().lower()
    validate_email_domain(email)

    purge_stale_otp_data()

    message = (
        f"If an account exists for {email}, a 6-digit reset code is on its way."
    )

    if delivery_mode() != "smtp":
        return {
            "message": "Password reset email is temporarily unavailable. Please try again later.",
            "verification_required": False,
            "email": email,
            "delivery": "unavailable",
        }

    donor = fetch_one(
        "SELECT id, full_name FROM donor_accounts WHERE email = %s",
        (email,),
    )

    # The code is requested before the account lookup so an unknown address
    # waits out exactly the same resend cooldown as a registered one. Asking
    # twice quickly returns 429 either way, which keeps this endpoint from
    # revealing who has an account.
    code = issue_code(email, "reset", RESET_CODE_TTL_MINUTES)

    if not donor:
        # No mail is sent: the cooldown row exists only to keep the timing and
        # status codes identical, and purge_stale_otp_data clears it later.
        return {
            "message": message,
            "verification_required": True,
            "email": email,
            "delivery": delivery_mode(),
            "expires_in_minutes": RESET_CODE_TTL_MINUTES,
            "resend_after_seconds": OTP_RESEND_SECONDS,
        }

    mode = send_code_email(
        email,
        "reset",
        code,
        donor["full_name"],
        RESET_CODE_TTL_MINUTES,
    )

    return code_issued_response(
        message,
        email,
        mode,
        code,
        RESET_CODE_TTL_MINUTES,
    )


@app.post("/donors/password/verify-code")
def verify_password_reset_code(data: EmailVerification):
    """Exchange a valid code for a short-lived reset token."""
    email = str(data.email).strip().lower()

    consume_code(email, "reset", data.code)

    if not fetch_one("SELECT id FROM donor_accounts WHERE email = %s", (email,)):
        raise HTTPException(status_code=400, detail="That code is not valid.")

    return {
        "message": "Code accepted. Choose a new password.",
        "reset_token": create_reset_token(email),
        "expires_in_minutes": RESET_TOKEN_TTL_MINUTES,
    }


@app.post("/donors/password/reset")
def reset_donor_password(data: PasswordReset):
    """Set a new password using the token from /donors/password/verify-code."""
    try:
        payload = decode_token(data.reset_token, DONOR_TOKEN_SECRET, "reset")
    except HTTPException:
        raise HTTPException(
            status_code=401,
            detail="The reset window has expired. Start the reset again.",
        )

    email = str(payload.get("sub") or "").strip().lower()

    donor = fetch_one(
        "SELECT id, password_hash FROM donor_accounts WHERE email = %s",
        (email,),
    )

    if not donor:
        raise HTTPException(status_code=404, detail="That account no longer exists.")

    if verify_password(data.new_password, donor["password_hash"]):
        raise HTTPException(
            status_code=400,
            detail="That is your current password. Choose a different one.",
        )

    execute_query(
        """
        UPDATE donor_accounts
        SET password_hash = %s, password_changed_at = NOW()
        WHERE id = %s
        """,
        (hash_password(data.new_password), donor["id"]),
    )

    return {
        "message": "Password updated. Sign in with your new password.",
        "email": email,
    }


@app.put("/donors/me/password")
def change_my_password(
    data: DonorPasswordChange,
    donor=Depends(get_current_donor),
):
    """Change the authenticated donor's password after verifying the current one."""
    account = fetch_one(
        "SELECT password_hash FROM donor_accounts WHERE id = %s",
        (donor["id"],),
    )
    if not account or not verify_password(data.current_password, account["password_hash"]):
        raise HTTPException(status_code=403, detail="Current password is incorrect.")

    if verify_password(data.new_password, account["password_hash"]):
        raise HTTPException(
            status_code=422,
            detail="Choose a new password different from your current password.",
        )

    execute_query(
        "UPDATE donor_accounts SET password_hash = %s, password_changed_at = NOW() WHERE id = %s",
        (hash_password(data.new_password), donor["id"]),
    )

    # Issue a fresh token so this browser stays signed in; older sessions are
    # rejected by get_current_donor using password_changed_at.
    access_token = create_token(str(donor["id"]), DONOR_TOKEN_SECRET, "donor")
    return {
        "message": "Password changed successfully.",
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 43200,
    }


@app.post("/donors/me/photo")
async def upload_my_profile_photo(
    photo: UploadFile = File(...),
    donor=Depends(get_current_donor),
):
    """Upload or replace the authenticated donor's profile photo."""
    extension = ALLOWED_PHOTO_TYPES.get(photo.content_type)

    if not extension:
        raise HTTPException(
            status_code=422,
            detail="Only JPG, PNG and WEBP images are allowed.",
        )

    contents = await photo.read(MAX_PHOTO_SIZE + 1)

    if not contents:
        raise HTTPException(status_code=422, detail="The selected image is empty.")

    if len(contents) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Profile photo must be smaller than 2 MB.",
        )

    valid_image = (
        (extension == ".jpg" and contents.startswith(b"\xff\xd8\xff"))
        or (extension == ".png" and contents.startswith(b"\x89PNG\r\n\x1a\n"))
        or (
            extension == ".webp"
            and contents.startswith(b"RIFF")
            and contents[8:12] == b"WEBP"
        )
    )

    if not valid_image:
        raise HTTPException(
            status_code=422,
            detail="The uploaded file is not a valid image.",
        )

    filename = f"donor_{donor['id']}_{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename
    old_path = donor.get("profile_photo_path")

    try:
        destination.write_bytes(contents)
        relative_path = f"/uploads/donor-photos/{filename}"

        execute_query(
            """
            UPDATE donor_accounts
            SET profile_photo_path = %s
            WHERE id = %s
            """,
            (relative_path, donor["id"]),
        )

        # Remove the photo this upload replaced.
        delete_photo_file(old_path)

        return {
            "message": "Profile photo uploaded successfully.",
            "profile_photo_url": relative_path,
        }

    except HTTPException:
        if destination.exists():
            destination.unlink()
        raise
    except Exception as exc:
        if destination.exists():
            destination.unlink()
        print(f"Profile photo upload failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=500,
            detail="Unable to save your profile photo.",
        )
    finally:
        await photo.close()


@app.delete("/donors/me/photo")
def remove_my_profile_photo(donor=Depends(get_current_donor)):
    """Remove the authenticated donor's profile photo."""
    old_path = donor.get("profile_photo_path")

    execute_query(
        """
        UPDATE donor_accounts
        SET profile_photo_path = NULL
        WHERE id = %s
        """,
        (donor["id"],),
    )

    if old_path:
        old_file = (BASE_DIR / old_path.lstrip("/")).resolve()
        if old_file.parent == UPLOAD_DIR.resolve() and old_file.is_file():
            old_file.unlink()

    return {
        "message": "Profile photo removed successfully.",
        "profile_photo_url": None,
    }


@app.get("/donors/me")
def get_my_profile(donor=Depends(get_current_donor)):
    photo_path = donor.get("profile_photo_path")
    donor["profile_photo_url"] = photo_path if photo_path else None
    return {"donor": donor}


@app.put("/donors/me")
def update_my_profile(
    data: DonorProfileUpdate,
    donor=Depends(get_current_donor),
):
    """Update editable details for the currently authenticated donor only."""
    full_name = data.full_name.strip()
    phone = data.phone.strip()
    location = data.location.strip()
    organization_name = (data.organization_name or "").strip() or None

    if len(full_name) < 2:
        raise HTTPException(status_code=422, detail="Please enter your full name.")
    if len(phone) < 7:
        raise HTTPException(status_code=422, detail="Please enter a valid phone number.")
    if len(location) < 2:
        raise HTTPException(status_code=422, detail="Please enter your location.")

    if donor["account_type"] == "Organization" and not organization_name:
        raise HTTPException(status_code=422, detail="Organization name is required.")
    if donor["account_type"] == "Individual":
        organization_name = None

    execute_query(
        """
        UPDATE donor_accounts
        SET full_name = %s,
            phone = %s,
            location = %s,
            organization_name = %s
        WHERE id = %s
        """,
        (full_name, phone, location, organization_name, donor["id"]),
    )

    updated_donor = fetch_one(
        """
        SELECT id, full_name, account_type, organization_name,
               email, phone, location, verification_status,
               email_verified, email_verified_at, created_at,
               profile_photo_path
        FROM donor_accounts
        WHERE id = %s
        """,
        (donor["id"],),
    )
    if not updated_donor:
        raise HTTPException(status_code=404, detail="Donor account not found.")

    photo_path = updated_donor.get("profile_photo_path")
    updated_donor["profile_photo_url"] = photo_path if photo_path else None

    return {"message": "Profile updated successfully.", "donor": updated_donor}


class DonorAccountDeletion(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=1, max_length=20)


@app.delete("/donors/me")
def delete_my_donor_account(
    data: DonorAccountDeletion,
    donor=Depends(get_current_donor),
):
    """Delete the authenticated donor account while retaining anonymized donation records."""
    if data.confirmation != "DELETE":
        raise HTTPException(
            status_code=400,
            detail='Type DELETE exactly to confirm account removal.',
        )

    account = fetch_one(
        "SELECT password_hash FROM donor_accounts WHERE id = %s",
        (donor["id"],),
    )
    if not account or not verify_password(data.password, account["password_hash"]):
        raise HTTPException(status_code=403, detail="Password is incorrect.")

    connection = get_connection()
    cursor = connection.cursor()
    try:
        # Keep donation/audit history, but remove identifying donor details.
        cursor.execute(
            """UPDATE donations
               SET donor_name = 'Deleted donor', contact = '',
                   status = CASE
                       WHEN status IN ('Available', 'Reserved') THEN 'Cancelled'
                       ELSE status
                   END,
                   donor_id = NULL
               WHERE donor_id = %s""",
            (donor["id"],),
        )
        cursor.execute(
            "DELETE FROM donor_accounts WHERE id = %s",
            (donor["id"],),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="Donor account was not found.")
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="Account deletion failed. No changes were saved.",
        )
    finally:
        cursor.close()
        connection.close()

    # The account row is gone, so its photo should not stay on disk.
    delete_photo_file(donor.get("profile_photo_path"))

    return {
        "message": "Your FoodShare account has been deleted. Your donation history was anonymized.",
        "account_deleted": True,
    }


# --------------------------------------------------
# DONOR DASHBOARD AND DONATIONS
# --------------------------------------------------


@app.get("/donors/me/donations")
def get_my_donations(donor=Depends(get_current_donor)):
    expire_stale_donations()

    donations = fetch_all(
        """
        SELECT
            id,
            donor_id,
            food_name,
            item_type,
            quantity,
            quantity_unit,
            category,
            location,
            pickup_deadline,
            donor_name,
            contact,
            expiry_date,
            packaging_condition,
            status,
            created_at
        FROM donations
        WHERE donor_id = %s
        ORDER BY created_at DESC
        """,
        (donor["id"],),
    )

    return {"donations": donations}



@app.get("/donors/me/dashboard")
def donor_dashboard(donor=Depends(get_current_donor)):
    expire_stale_donations()

    summary = fetch_one(
        """
        SELECT
            COUNT(*) AS total_donations,
            COALESCE(SUM(CASE WHEN status = 'Available' THEN 1 ELSE 0 END), 0)
                AS available_donations,
            COALESCE(SUM(CASE WHEN status = 'Reserved' THEN 1 ELSE 0 END), 0)
                AS reserved_donations,
            COALESCE(SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END), 0)
                AS completed_donations,
            COALESCE(SUM(CASE WHEN status = 'Cancelled' THEN 1 ELSE 0 END), 0)
                AS cancelled_donations,
            COALESCE(SUM(CASE WHEN status = 'Expired' THEN 1 ELSE 0 END), 0)
                AS expired_donations
        FROM donations
        WHERE donor_id = %s
        """,
        (donor["id"],),
    )

    # The dashboard shows the donor avatar, so hand back a ready-to-use URL.
    donor_payload = dict(donor)
    donor_payload["profile_photo_url"] = donor.get("profile_photo_path") or None

    return {
        "donor": donor_payload,
        "summary": summary,
        "statistics": summary,
    }


@app.post("/donations", status_code=201)
def create_donation(
    data: DonationCreate,
    donor=Depends(get_current_donor),
):
    now = datetime.now()
    pickup_deadline = to_server_local(data.pickup_deadline)
    expiry_date = to_server_local(data.expiry_date)

    if pickup_deadline is None or pickup_deadline <= now:
        raise HTTPException(
            status_code=422,
            detail="Pickup deadline must be in the future.",
        )

    if expiry_date and expiry_date.date() < now.date():
        raise HTTPException(
            status_code=422,
            detail="Expiry date cannot be in the past.",
        )

    # The donor may post a different name/number for this listing; when they
    # leave the fields out, fall back to the account they signed in with.
    donor_name = (data.donor_name or "").strip() or donor["full_name"]
    contact = (data.contact or "").strip() or donor["phone"]

    if len(contact) < 7:
        raise HTTPException(
            status_code=422,
            detail="Enter a contact number with at least 7 characters.",
        )

    donation_id, _ = execute_query(
        """
        INSERT INTO donations
            (donor_id, food_name, item_type, quantity, quantity_unit,
             category, location, pickup_deadline, donor_name, contact,
             expiry_date, packaging_condition, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            donor["id"],
            data.food_name.strip(),
            data.item_type.strip(),
            data.quantity,
            data.quantity_unit.strip(),
            data.category.strip(),
            data.location.strip(),
            pickup_deadline,
            donor_name,
            contact,
            expiry_date.date() if expiry_date else None,
            data.packaging_condition,
            "Available",
        ),
    )

    return {
        "message": "Donation created successfully.",
        "donation_id": donation_id,
    }


@app.put("/donors/me/donations/{donation_id}")
def update_my_donation(
    donation_id: int,
    data: DonationUpdate,
    donor=Depends(get_current_donor),
):
    existing = fetch_one(
        """
        SELECT id, status
        FROM donations
        WHERE id = %s AND donor_id = %s
        """,
        (donation_id, donor["id"]),
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Donation not found.")

    if existing["status"] != "Available":
        raise HTTPException(
            status_code=400,
            detail="Only available donations can be edited.",
        )

    updates = {
        key: value
        for key, value in data.model_dump(exclude_unset=True).items()
        if key in EDITABLE_DONATION_FIELDS
    }

    # Drop explicit nulls for the NOT NULL columns instead of forwarding them.
    updates = {
        key: value
        for key, value in updates.items()
        if value is not None or key in NULLABLE_DONATION_FIELDS
    }

    for key in REQUIRED_DONATION_TEXT_FIELDS:
        if isinstance(updates.get(key), str):
            updates[key] = updates[key].strip()

            if not updates[key]:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key.replace('_', ' ').capitalize()} cannot be empty.",
                )

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    if "quantity" in updates and updates["quantity"] is not None:
        if updates["quantity"] <= 0:
            raise HTTPException(status_code=422, detail="Quantity must be positive.")

    if "pickup_deadline" in updates and updates["pickup_deadline"] is not None:
        deadline = to_server_local(updates["pickup_deadline"])
        if deadline <= datetime.now():
            raise HTTPException(
                status_code=422,
                detail="Pickup deadline must be in the future.",
            )
        updates["pickup_deadline"] = deadline

    if "expiry_date" in updates and updates["expiry_date"] is not None:
        expiry = to_server_local(updates["expiry_date"])
        # Same rule as creation, so an edit cannot backdate an expiry.
        if expiry.date() < datetime.now().date():
            raise HTTPException(
                status_code=422,
                detail="Expiry date cannot be in the past.",
            )
        updates["expiry_date"] = expiry.date()

    assignments = ", ".join(f"{column} = %s" for column in updates)
    values = list(updates.values()) + [donation_id, donor["id"]]

    execute_query(
        f"""
        UPDATE donations
        SET {assignments}
        WHERE id = %s AND donor_id = %s
        """,
        tuple(values),
    )

    return {"message": "Donation updated successfully."}


@app.patch("/donors/me/donations/{donation_id}/complete")
def complete_my_donation(
    donation_id: int,
    donor=Depends(get_current_donor),
):
    existing = fetch_one(
        """
        SELECT id, status FROM donations
        WHERE id = %s AND donor_id = %s
        """,
        (donation_id, donor["id"]),
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Donation not found.")

    if existing["status"] not in ("Available", "Reserved"):
        raise HTTPException(
            status_code=400,
            detail="This donation cannot be marked completed.",
        )

    execute_query(
        """
        UPDATE donations SET status = 'Completed'
        WHERE id = %s AND donor_id = %s
        """,
        (donation_id, donor["id"]),
    )

    return {"message": "Donation marked as completed."}


@app.patch("/donors/me/donations/{donation_id}/cancel")
def cancel_my_donation(
    donation_id: int,
    donor=Depends(get_current_donor),
):
    existing = fetch_one(
        """
        SELECT id, status FROM donations
        WHERE id = %s AND donor_id = %s
        """,
        (donation_id, donor["id"]),
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Donation not found.")

    if existing["status"] != "Available":
        raise HTTPException(
            status_code=400,
            detail="Only available donations can be cancelled.",
        )

    execute_query(
        """
        UPDATE donations SET status = 'Cancelled'
        WHERE id = %s AND donor_id = %s
        """,
        (donation_id, donor["id"]),
    )

    return {"message": "Donation cancelled successfully."}


# --------------------------------------------------
# PUBLIC FOOD LISTINGS
# --------------------------------------------------

@app.get("/donations")
def list_donations(
    category: Optional[str] = Query(default=None),
    location: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
):
    expire_stale_donations()

    sql = """
        SELECT id, food_name, item_type, quantity, quantity_unit,
               category, location, pickup_deadline, donor_name,
               contact, expiry_date, packaging_condition, status, created_at
        FROM donations
        WHERE status = 'Available'
          AND pickup_deadline > NOW()
          AND (expiry_date IS NULL OR expiry_date >= CURDATE())
    """
    params = []

    if category:
        sql += " AND category = %s"
        params.append(category)

    if location:
        sql += " AND location LIKE %s"
        params.append(f"%{location}%")

    if search:
        sql += " AND (food_name LIKE %s OR item_type LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    sql += " ORDER BY created_at DESC"

    return {"donations": fetch_all(sql, tuple(params))}


@app.get("/donations/{donation_id}")
@limiter.limit(DETAIL_RATE_LIMIT)
def get_donation(request: Request, response: Response, donation_id: int):
    # `response` is required by the limiter, which writes the rate-limit headers
    # into it; see the note on login_donor.
    donation = fetch_one(
        """
        SELECT id, food_name, item_type, quantity, quantity_unit,
               category, location, pickup_deadline, donor_name,
               contact, expiry_date, packaging_condition, status, created_at
        FROM donations
        WHERE id = %s
        """,
        (donation_id,),
    )

    if not donation:
        raise HTTPException(status_code=404, detail="Donation not found.")

    # The number is published only while the food can still be collected, which
    # is the same set of donations the public listing already shows. Once a
    # donation is completed, cancelled or expired its donor's number stops being
    # readable, so walking the id space cannot harvest phone numbers.
    contact_visible = donation["status"] in CONTACT_VISIBLE_STATUSES

    if not contact_visible:
        donation["contact"] = None

    return {"donation": donation, "contact_visible": contact_visible}


# --------------------------------------------------
# FOOD REQUESTS
# --------------------------------------------------

@app.post("/food-requests", status_code=201)
def create_food_request(data: FoodRequestCreate):
    request_id, _ = execute_query(
        """
        INSERT INTO food_requests
            (requester_name, contact, location, food_category, quantity_needed)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            data.requester_name.strip(),
            data.contact.strip(),
            data.location.strip(),
            data.food_category.strip(),
            data.quantity_needed,
        ),
    )

    return {
        "message": "Food request submitted successfully.",
        "request_id": request_id,
    }


@app.get("/food-requests")
def list_food_requests(admin=Depends(get_current_admin)):
    return {
        "requests": fetch_all(
            "SELECT * FROM food_requests ORDER BY created_at DESC"
        )
    }


@app.patch("/food-requests/{request_id}/status")
def update_food_request_status(
    request_id: int,
    data: FoodRequestStatusUpdate,
    admin=Depends(get_current_admin),
):
    existing = fetch_one(
        "SELECT id FROM food_requests WHERE id = %s",
        (request_id,),
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Food request not found.")

    execute_query(
        "UPDATE food_requests SET request_status = %s WHERE id = %s",
        (data.request_status, request_id),
    )

    return {
        "message": "Food request status updated.",
        "request_id": request_id,
        "request_status": data.request_status,
    }


# --------------------------------------------------
# ADMIN LOGIN
# --------------------------------------------------

@app.post("/admin/login")
@limiter.limit(LOGIN_RATE_LIMIT)
def login_admin(request: Request, response: Response, data: AdminLogin):
    # `response` is required, not unused: the limiter writes X-RateLimit-*
    # headers into it. Without it slowapi raises on the successful login path.
    if not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
        raise HTTPException(
            status_code=500,
            detail="Admin credentials are not configured.",
        )

    if (
        not hmac.compare_digest(data.username, ADMIN_USERNAME)
        or not verify_password(data.password, ADMIN_PASSWORD_HASH)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin username or password.",
        )

    token = create_token(
        ADMIN_USERNAME,
        ADMIN_TOKEN_SECRET,
        "admin",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 43200,
    }


# --------------------------------------------------
# ADMIN EMAIL DELIVERY
# --------------------------------------------------

@app.get("/admin/mail/status")
def admin_mail_status(admin=Depends(get_current_admin)):
    """Report how signup and reset codes leave the server."""
    return {"mail": mail_status()}


@app.post("/admin/mail/test")
def admin_mail_test(
    data: MailTestRequest,
    admin=Depends(get_current_admin),
):
    """Send one real message so delivery can be confirmed without signing up."""
    info = mail_status()

    if info["mode"] != "smtp":
        raise HTTPException(
            status_code=400,
            detail=info["reason"]
            or "Email delivery is not configured, so no message was sent.",
        )

    try:
        send_test(str(data.email).strip())
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The mail server did not accept the message: {exc}",
        )

    return {
        "message": (
            f"{info['host']} accepted a test message for {data.email}. "
            "Check that inbox, including the spam folder."
        ),
        "mail": info,
    }


# --------------------------------------------------
# ADMIN DONOR AND ORGANIZATION MANAGEMENT
# --------------------------------------------------

@app.get("/admin/registrations")
def admin_registrations(admin=Depends(get_current_admin)):
    registrations = fetch_all(
        """
        SELECT id, full_name, account_type, organization_name,
               email, phone, location, verification_status,
               email_verified, email_verified_at, created_at
        FROM donor_accounts
        ORDER BY created_at DESC
        """
    )
    return {"registrations": registrations, "donors": registrations}


@app.get("/admin/donors")
def admin_donors(admin=Depends(get_current_admin)):
    return {
        "donors": fetch_all(
            """
            SELECT id, full_name, account_type, organization_name,
                   email, phone, location, verification_status,
                   email_verified, email_verified_at, created_at
            FROM donor_accounts
            ORDER BY created_at DESC
            """
        )
    }


@app.patch("/admin/organizations/{donor_id}/verification")
def verify_organization(
    donor_id: int,
    data: OrganizationVerification,
    admin=Depends(get_current_admin),
):
    donor = fetch_one(
        "SELECT id, account_type FROM donor_accounts WHERE id = %s",
        (donor_id,),
    )

    if not donor:
        raise HTTPException(status_code=404, detail="Donor not found.")

    if donor["account_type"] != "Organization":
        raise HTTPException(
            status_code=400,
            detail="This account is not an organization.",
        )

    execute_query(
        """
        UPDATE donor_accounts
        SET verification_status = %s
        WHERE id = %s
        """,
        (data.verification_status, donor_id),
    )

    return {"message": "Organization verification status updated."}


# --------------------------------------------------
# ADMIN DONATION MANAGEMENT
# --------------------------------------------------

@app.get("/admin/donations")
def admin_donations(admin=Depends(get_current_admin)):
    expire_stale_donations()

    donations = fetch_all(
        """
        SELECT d.*, a.email AS donor_email,
               a.account_type AS donor_account_type,
               a.verification_status AS donor_verification_status
        FROM donations d
        LEFT JOIN donor_accounts a ON a.id = d.donor_id
        ORDER BY d.created_at DESC
        """
    )
    return {"donations": donations}


@app.patch("/admin/donations/{donation_id}/status")
def admin_update_donation_status(
    donation_id: int,
    data: DonationStatusUpdate,
    admin=Depends(get_current_admin),
):
    donation = fetch_one(
        "SELECT id FROM donations WHERE id = %s",
        (donation_id,),
    )

    if not donation:
        raise HTTPException(status_code=404, detail="Donation not found.")

    execute_query(
        "UPDATE donations SET status = %s WHERE id = %s",
        (data.status, donation_id),
    )

    return {"message": "Donation status updated successfully."}


@app.delete("/admin/donations/{donation_id}")
def admin_delete_donation(
    donation_id: int,
    admin=Depends(get_current_admin),
):
    _, row_count = execute_query(
        "DELETE FROM donations WHERE id = %s",
        (donation_id,),
    )

    if row_count == 0:
        raise HTTPException(status_code=404, detail="Donation not found.")

    return {"message": "Donation deleted successfully."}



@app.patch("/admin/donations/{donation_id}/complete")
def admin_complete_donation(
    donation_id: int,
    admin=Depends(get_current_admin),
):
    donation = fetch_one(
        """
        SELECT id, status
        FROM donations
        WHERE id = %s
        """,
        (donation_id,),
    )

    if not donation:
        raise HTTPException(
            status_code=404,
            detail="Donation not found.",
        )

    if donation["status"] == "Completed":
        return {
            "message": "Donation is already completed.",
            "donation_id": donation_id,
            "status": "Completed",
        }

    if donation["status"] not in ("Available", "Reserved"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete a donation with status '{donation['status']}'.",
        )

    execute_query(
        """
        UPDATE donations
        SET status = 'Completed'
        WHERE id = %s
        """,
        (donation_id,),
    )

    return {
        "message": "Donation marked as completed successfully.",
        "donation_id": donation_id,
        "status": "Completed",
    }


# Wrap the complete FastAPI application so CORS headers are also added to
# unexpected 500 responses generated by Starlette's outer error middleware.
app = CORSMiddleware(
    app=app,
    allow_origins=["null", *configured_origins],
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# APPLICATION ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )

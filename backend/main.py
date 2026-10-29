
import os
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

import jwt
import mysql.connector
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, EmailStr
from mysql.connector import Error

from database import get_connection, initialize_database
from emailer import (
    EmailDeliveryError,
    delivery_mode,
    send_otp,
    send_test,
    status as mail_status,
)

load_dotenv()

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

# The static frontend is served by Live Server / http.server, which can pick
# any local port, and may also be opened straight from disk (origin "null").
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DONOR_TOKEN_SECRET = os.getenv("DONOR_TOKEN_SECRET", "")
ADMIN_TOKEN_SECRET = os.getenv("ADMIN_TOKEN_SECRET", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

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


def get_bearer_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization")
):
    """Read the Authorization header and return the JWT token."""
    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=401,
            detail="Authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    value = authorization.strip()

    # Standard format is: Authorization: Bearer <token>.
    # Also accept a raw token for compatibility with the existing Swagger field.
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
    else:
        token = value

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


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
               created_at
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
        return send_otp(email, code, purpose, recipient_name, ttl_minutes)
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

    if mode == "demo":
        # No SMTP configured: without the code here the flow cannot continue.
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

class DonorRegister(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    account_type: Literal["Individual", "Organization"] = "Individual"
    organization_name: Optional[str] = Field(default=None, max_length=200)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=30)
    location: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class DonationCreate(BaseModel):
    food_name: str = Field(min_length=2, max_length=200)
    item_type: str = Field(min_length=1, max_length=100)
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
    item_type: Optional[str] = Field(default=None, max_length=100)
    quantity: Optional[float] = Field(default=None, gt=0, le=1000000)
    quantity_unit: Optional[str] = Field(default=None, max_length=30)
    category: Optional[str] = Field(default=None, max_length=100)
    location: Optional[str] = Field(default=None, max_length=255)
    pickup_deadline: Optional[datetime] = None
    donor_name: Optional[str] = Field(default=None, max_length=150)
    contact: Optional[str] = Field(default=None, max_length=30)
    expiry_date: Optional[datetime] = None
    packaging_condition: Optional[str] = Field(default=None, max_length=100)


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

    consume_code(email, "signup", data.code)

    if fetch_one("SELECT id FROM donor_accounts WHERE email = %s", (email,)):
        execute_query(
            "DELETE FROM pending_registrations WHERE email = %s",
            (email,),
        )
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

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


@app.post("/donors/login")
def login_donor(form: OAuth2PasswordRequestForm = Depends()):
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

    purge_stale_otp_data()

    message = (
        f"If an account exists for {email}, a 6-digit reset code is on its way."
    )

    donor = fetch_one(
        "SELECT id, full_name FROM donor_accounts WHERE email = %s",
        (email,),
    )

    if not donor:
        return {
            "message": message,
            "verification_required": True,
            "email": email,
            "delivery": delivery_mode(),
            "expires_in_minutes": RESET_CODE_TTL_MINUTES,
            "resend_after_seconds": OTP_RESEND_SECONDS,
        }

    code = issue_code(email, "reset", RESET_CODE_TTL_MINUTES)
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


@app.get("/donors/me")
def get_my_profile(donor=Depends(get_current_donor)):
    return {"donor": donor}


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

    return {
        "donor": donor,
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
            donor["full_name"],
            donor["phone"],
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

    updates = data.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    if "quantity" in updates and updates["quantity"] is not None:
        if updates["quantity"] <= 0:
            raise HTTPException(status_code=422, detail="Quantity must be positive.")

    allowed_fields = {
        "food_name", "item_type", "quantity", "quantity_unit",
        "category", "location", "pickup_deadline", "donor_name",
        "contact", "expiry_date", "packaging_condition",
    }

    updates = {
        key: value
        for key, value in updates.items()
        if key in allowed_fields
    }

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update.")

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
def get_donation(donation_id: int):
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

    return {"donation": donation}


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
def login_admin(data: AdminLogin):
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

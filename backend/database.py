
import os
import re
import threading
import time
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error, pooling
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# SCHEMA SYNC
# --------------------------------------------------

ENUM_COLUMNS = [
    {
        "table": "donations",
        "column": "status",
        "values": ["Available", "Reserved", "Completed", "Expired", "Cancelled"],
        "default": "Available",
        "renames": {"Approved": "Available", "Rejected": "Cancelled"},
    },
    {
        "table": "donor_accounts",
        "column": "verification_status",
        "values": ["Pending", "Verified", "Rejected"],
        "default": "Pending",
        "renames": {"Not Required": "Verified"},
    },
    {
        "table": "food_requests",
        "column": "request_status",
        "values": ["Pending", "Approved", "Fulfilled", "Rejected"],
        "default": "Pending",
        "renames": {},
    },
]

COLUMN_ADDITIONS = [
    (
        "donations",
        "updated_at",
        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP",
    ),
    ("donor_accounts", "email_verified", "TINYINT(1) NOT NULL DEFAULT 1"),
    ("donor_accounts", "email_verified_at", "DATETIME NULL"),
    ("donor_accounts", "password_changed_at", "DATETIME NULL"),
    ("donor_accounts", "google_sub", "VARCHAR(255) NULL"),
    ("donor_accounts", "profile_photo_path", "VARCHAR(500) NULL"),
]

# Older installs were created with shorter VARCHARs than the code validates
# against. A value that passes validation but does not fit the column makes
# MySQL answer "Data truncated for column ..." (a 500), so widen them to the
# width declared by the CREATE statements above.
COLUMN_WIDENINGS = [
    ("donations", "food_name", "VARCHAR(200) NOT NULL"),
    ("donations", "quantity_unit", "VARCHAR(30) NOT NULL DEFAULT 'items'"),
    ("donations", "location", "VARCHAR(255) NOT NULL"),
    ("donor_accounts", "organization_name", "VARCHAR(200) NULL"),
    ("donor_accounts", "location", "VARCHAR(255) NOT NULL"),
]

# Secondary indexes declared by the CREATE TABLE statements below. Databases
# created before those statements existed have none of them, which turns every
# status filter, category filter and donor lookup into a full table scan.
INDEXES = [
    ("donations", "idx_donations_donor_id", "donor_id"),
    ("donations", "idx_donations_status", "status"),
    ("donations", "idx_donations_category", "category"),
]

def _column_type(cursor, table, column):
    cursor.execute(
        """
        SELECT COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _has_column(cursor, table, column):
    return _column_type(cursor, table, column) is not None


def _sync_enum_column(cursor, spec):
    table = spec["table"]
    column = spec["column"]
    values = spec["values"]
    current = _column_type(cursor, table, column)

    if current is None:
        return None

    if set(re.findall(r"'([^']*)'", current)) == set(values):
        return None

    for old_value, new_value in spec["renames"].items():
        if old_value in current and new_value in values:
            cursor.execute(
                f"UPDATE {table} SET {column} = %s WHERE {column} = %s",
                (new_value, old_value),
            )

    cursor.execute(
        f"UPDATE {table} SET {column} = %s "
        f"WHERE {column} IS NULL OR {column} NOT IN "
        f"({','.join(['%s'] * len(values))})",
        tuple([spec["default"], *values]),
    )

    enum_definition = ",".join(f"'{value}'" for value in values)

    cursor.execute(
        f"ALTER TABLE {table} MODIFY COLUMN {column} "
        f"ENUM({enum_definition}) NOT NULL DEFAULT '{spec['default']}'"
    )

    return f"{table}.{column}"


def _has_index(cursor, table, index_name):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table, index_name),
    )

    return cursor.fetchone()[0] > 0


def _ensure_index(cursor, table, index_name, columns):
    """Add a missing secondary index; return its name only when created."""
    if _has_index(cursor, table, index_name):
        return None

    cursor.execute(f"ALTER TABLE {table} ADD INDEX {index_name} ({columns})")

    return f"{table}.{index_name}"


def _widen_column(cursor, table, column, definition):
    """Enlarge a VARCHAR column that is narrower than the current schema."""
    current = _column_type(cursor, table, column)

    if current is None:
        return None

    target = re.search(r"VARCHAR\((\d+)\)", definition, re.IGNORECASE)
    existing = re.match(r"varchar\((\d+)\)", current.strip(), re.IGNORECASE)

    # ENUM and other non-VARCHAR columns are managed by their own sync rules.
    if not target or not existing:
        return None

    if int(existing.group(1)) >= int(target.group(1)):
        return None

    cursor.execute(f"ALTER TABLE {table} MODIFY COLUMN {column} {definition}")

    return f"{table}.{column}"


def sync_schema(connection, cursor):
    """Bring an existing database up to the schema this code expects."""
    changes = []

    for table, column, definition in COLUMN_ADDITIONS:
        if not _has_column(cursor, table, column):
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
            changes.append(f"{table}.{column}")

    for table, column, definition in COLUMN_WIDENINGS:
        widened = _widen_column(cursor, table, column, definition)

        if widened:
            changes.append(f"{widened} (widened)")

    if not _has_index(cursor, "donor_accounts", "uq_donor_google_sub"):
        cursor.execute(
            """
            ALTER TABLE donor_accounts
            ADD UNIQUE INDEX uq_donor_google_sub (google_sub)
            """
        )
        changes.append("donor_accounts.google_sub unique index")

    for table, index_name, columns in INDEXES:
        created = _ensure_index(cursor, table, index_name, columns)

        if created:
            changes.append(created)

    for spec in ENUM_COLUMNS:
        changed = _sync_enum_column(cursor, spec)
        if changed:
            changes.append(changed)

    if changes:
        connection.commit()
        print("Schema updated: " + ", ".join(changes))

    return changes


# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------

def get_db_config():
    """Read MySQL configuration from environment variables."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "foodshare"),
    }


POOL_NAME = "foodshare_pool"
DEFAULT_POOL_SIZE = 10
POOL_WAIT_SECONDS = 5.0
POOL_RETRY_DELAY = 0.02

_pool = None
_pool_lock = threading.Lock()


def get_pool_size():
    """Pool size from DB_POOL_SIZE, clamped to what the driver allows."""
    try:
        size = int(os.getenv("DB_POOL_SIZE", "") or DEFAULT_POOL_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_POOL_SIZE

    return max(1, min(size, pooling.CNX_POOL_MAXSIZE))


def get_pool():
    """Create the pool on first use.

    Building it lazily keeps a database outage from breaking the import: the
    API starts, and /health reports the connection problem instead.
    """
    global _pool

    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is None:
            try:
                _pool = pooling.MySQLConnectionPool(
                    pool_name=POOL_NAME,
                    pool_size=get_pool_size(),
                    # Rolls back anything left open when a connection is
                    # returned, so one request cannot inherit another's state.
                    pool_reset_session=True,
                    **get_db_config(),
                )
            except Error as exc:
                raise RuntimeError(
                    f"MySQL connection pool could not start: {exc}"
                ) from exc

    return _pool


def get_connection():
    """Borrow a pooled connection. Close it to hand it back to the pool."""
    pool = get_pool()
    give_up_at = time.monotonic() + POOL_WAIT_SECONDS

    while True:
        try:
            return pool.get_connection()
        except pooling.PoolError as exc:
            # The driver does not wait for a free connection, so a burst of
            # requests would otherwise surface as a 500.
            if time.monotonic() >= give_up_at:
                raise RuntimeError(
                    "MySQL is busy: no pooled connection became available."
                ) from exc

            time.sleep(POOL_RETRY_DELAY)
        except Error as exc:
            raise RuntimeError(
                f"MySQL connection failed: {exc}"
            ) from exc


@contextmanager
def database_cursor(dictionary=True):
    """Manage a database connection and cursor safely."""
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)

    try:
        yield connection, cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

def initialize_database():
    """Create FoodShare tables and apply safe schema additions."""

    statements = [
        """
        CREATE TABLE IF NOT EXISTS donor_accounts (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            full_name VARCHAR(150) NOT NULL,
            account_type ENUM(
                'Individual',
                'Organization'
            ) NOT NULL DEFAULT 'Individual',
            organization_name VARCHAR(200) NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            phone VARCHAR(30) NOT NULL,
            location VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            verification_status ENUM(
                'Pending',
                'Verified',
                'Rejected'
            ) NOT NULL DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS donations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            donor_id BIGINT NULL,
            food_name VARCHAR(200) NOT NULL,
            item_type VARCHAR(100) NOT NULL,
            quantity DECIMAL(10, 2) NOT NULL,
            quantity_unit VARCHAR(30) NOT NULL DEFAULT 'items',
            category VARCHAR(100) NOT NULL,
            location VARCHAR(255) NOT NULL,
            pickup_deadline DATETIME NOT NULL,
            donor_name VARCHAR(150) NOT NULL,
            contact VARCHAR(30) NOT NULL,
            expiry_date DATE NULL,
            packaging_condition VARCHAR(100) NULL,
            status ENUM(
                'Available',
                'Reserved',
                'Completed',
                'Expired',
                'Cancelled'
            ) NOT NULL DEFAULT 'Available',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_donations_donor_id (donor_id),
            INDEX idx_donations_status (status),
            INDEX idx_donations_category (category),
            CONSTRAINT fk_donations_donor
                FOREIGN KEY (donor_id)
                REFERENCES donor_accounts(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS food_requests (
            id INT AUTO_INCREMENT PRIMARY KEY,
            requester_name VARCHAR(150) NOT NULL,
            contact VARCHAR(30) NOT NULL,
            location VARCHAR(255) NOT NULL,
            food_category VARCHAR(100) NOT NULL,
            quantity_needed DECIMAL(10, 2) NOT NULL,
            request_status ENUM(
                'Pending',
                'Approved',
                'Fulfilled',
                'Rejected'
            ) NOT NULL DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS admin_accounts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS pending_registrations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            full_name VARCHAR(150) NOT NULL,
            account_type ENUM(
                'Individual',
                'Organization'
            ) NOT NULL DEFAULT 'Individual',
            organization_name VARCHAR(200) NULL,
            phone VARCHAR(30) NOT NULL,
            location VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS email_otps (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            purpose ENUM('signup', 'reset') NOT NULL,
            code_hash CHAR(64) NOT NULL,
            attempts INT NOT NULL DEFAULT 0,
            expires_at DATETIME NOT NULL,
            consumed_at DATETIME NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_email_otps_lookup (email, purpose, consumed_at),
            INDEX idx_email_otps_expiry (expires_at)
        ) ENGINE=InnoDB
        """,

        """
        CREATE TABLE IF NOT EXISTS donor_welcome_emails (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            donor_id BIGINT NOT NULL,
            email VARCHAR(255) NOT NULL,

            status ENUM(
                'Pending',
                'Sent',
                'Failed'
            ) NOT NULL DEFAULT 'Pending',

            attempts INT NOT NULL DEFAULT 0,
            sent_at DATETIME NULL,
            last_error VARCHAR(500) NULL,

            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,

            UNIQUE KEY uq_donor_welcome_email_donor (donor_id),

            CONSTRAINT fk_welcome_email_donor
                FOREIGN KEY (donor_id)
                REFERENCES donor_accounts(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB
        """,
    ]

    connection = get_connection()
    cursor = connection.cursor()

    try:
        for statement in statements:
            cursor.execute(statement)

        sync_schema(connection, cursor)

        connection.commit()
        print("FoodShare MySQL tables initialized successfully.")

    except Error:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    initialize_database()


import os
import re
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# SCHEMA SYNC
# --------------------------------------------------
# CREATE TABLE IF NOT EXISTS never changes a table that already exists, so a
# database created by an older version of FoodShare keeps its old ENUM values
# and older columns. Without the sync below, writing a status such as
# 'Cancelled' fails with "Data truncated for column 'status'".
#
# Each entry is applied only when the live column differs from the expected
# definition. "renames" maps values that no longer exist to their replacement
# so the ALTER never has to drop a value that is still in use.

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
    # A donor account is only created once its email code has been entered, so
    # every row that exists is verified. The default keeps accounts created by
    # an older version of FoodShare (which had no verification step) usable.
    ("donor_accounts", "email_verified", "TINYINT(1) NOT NULL DEFAULT 1"),
    ("donor_accounts", "email_verified_at", "DATETIME NULL"),
    # Set when a password is reset. Tokens issued before that moment are
    # rejected, so a reset also signs out browsers still using the old password.
    ("donor_accounts", "password_changed_at", "DATETIME NULL"),
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
        f"WHERE {column} IS NULL OR {column} NOT IN ({','.join(['%s'] * len(values))})",
        tuple([spec["default"], *values]),
    )

    enum_definition = ",".join(f"'{value}'" for value in values)

    cursor.execute(
        f"ALTER TABLE {table} MODIFY COLUMN {column} "
        f"ENUM({enum_definition}) NOT NULL DEFAULT '{spec['default']}'"
    )

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

    for spec in ENUM_COLUMNS:
        changed = _sync_enum_column(cursor, spec)

        if changed:
            changes.append(changed)

    if changes:
        connection.commit()
        print("Schema updated: " + ", ".join(changes))

    return changes


def get_db_config():
    """Read MySQL configuration from environment variables."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "foodshare"),
    }


def get_connection():
    """Create and return a MySQL database connection."""
    try:
        connection = mysql.connector.connect(**get_db_config())
        if connection.is_connected():
            return connection

        raise RuntimeError("Unable to connect to MySQL.")

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


def initialize_database():
    """Create the FoodShare tables if they do not exist."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS donor_accounts (
            id INT AUTO_INCREMENT PRIMARY KEY,
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
            donor_id INT NULL,
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
        # A sign-up is parked here until the emailed code is entered. The
        # account itself is created by /donors/register/verify, so an
        # abandoned sign-up never leaves a usable half-account behind.
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
        # Only a keyed hash of the code is stored: a database dump cannot be
        # turned back into working codes without the application secret.
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
        """
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

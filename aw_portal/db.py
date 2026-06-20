from __future__ import annotations

import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "aw_portal.sqlite3"


class PortalConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb) -> bool:
        result = super().__exit__(exc_type, exc, tb)
        self.close()
        return result


def database_path() -> Path:
    return Path(os.environ.get("RAILWAY_DATABASE_PATH", DEFAULT_DB_PATH))


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, factory=PortalConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            household_name TEXT NOT NULL,
            monthly_salary TEXT NOT NULL DEFAULT '0',
            monthly_expense_budget TEXT NOT NULL DEFAULT '0',
            insurance_deductibles TEXT NOT NULL DEFAULT '0',
            private_reserve_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            email TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            person_index INTEGER NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            dob TEXT NOT NULL DEFAULT '',
            ssn_last4 TEXT NOT NULL DEFAULT '',
            UNIQUE(client_id, person_index)
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            owner_index INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL,
            account_type TEXT NOT NULL,
            institution TEXT NOT NULL DEFAULT '',
            account_last4 TEXT NOT NULL DEFAULT '',
            display_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS liabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            liability_type TEXT NOT NULL,
            interest_rate TEXT NOT NULL DEFAULT '',
            account_last4 TEXT NOT NULL DEFAULT '',
            display_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trust_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            property_address TEXT NOT NULL DEFAULT '',
            display_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS quarterly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            report_date TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS report_account_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES quarterly_reports(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            balance TEXT NOT NULL,
            cash_balance TEXT NOT NULL DEFAULT '0'
        );

        CREATE TABLE IF NOT EXISTS generated_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES quarterly_reports(id) ON DELETE CASCADE,
            file_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS deductible_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            amount TEXT NOT NULL DEFAULT '0',
            display_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS report_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            report_date TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS export_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            report_id INTEGER NOT NULL REFERENCES quarterly_reports(id) ON DELETE CASCADE,
            export_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            external_url TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    apply_lightweight_migrations(conn)
    conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}


def apply_lightweight_migrations(conn: sqlite3.Connection) -> None:
    ensure_company_seed(conn)
    migrate_users_role_constraint(conn)
    add_column(conn, "users", "company_id", "INTEGER REFERENCES companies(id) ON DELETE CASCADE")
    add_column(conn, "clients", "company_id", "INTEGER REFERENCES companies(id) ON DELETE CASCADE")
    add_column(conn, "clients", "household_type", "TEXT NOT NULL DEFAULT 'married'")
    add_column(conn, "clients", "private_reserve_target_override", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "clients", "source_notes", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "clients", "archive_status", "TEXT NOT NULL DEFAULT 'active'")
    add_column(conn, "accounts", "floor_amount", "TEXT NOT NULL DEFAULT '1000'")
    add_column(conn, "accounts", "source_notes", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "liabilities", "lender", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "liabilities", "source_notes", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "quarterly_reports", "status", "TEXT NOT NULL DEFAULT 'generated'")
    add_column(conn, "quarterly_reports", "export_status", "TEXT NOT NULL DEFAULT 'not_configured'")
    add_column(conn, "quarterly_reports", "company_id", "INTEGER REFERENCES companies(id) ON DELETE CASCADE")
    add_column(conn, "report_drafts", "company_id", "INTEGER REFERENCES companies(id) ON DELETE CASCADE")
    add_column(conn, "audit_events", "company_id", "INTEGER REFERENCES companies(id) ON DELETE SET NULL")
    add_column(conn, "export_jobs", "company_id", "INTEGER REFERENCES companies(id) ON DELETE CASCADE")
    default_company_id = ensure_company_seed(conn)
    conn.execute("UPDATE users SET company_id = ? WHERE company_id IS NULL", (default_company_id,))
    conn.execute("UPDATE clients SET company_id = ? WHERE company_id IS NULL", (default_company_id,))
    conn.execute("UPDATE quarterly_reports SET company_id = (SELECT company_id FROM clients WHERE clients.id = quarterly_reports.client_id) WHERE company_id IS NULL")
    conn.execute("UPDATE report_drafts SET company_id = (SELECT company_id FROM clients WHERE clients.id = report_drafts.client_id) WHERE company_id IS NULL")
    conn.execute("UPDATE audit_events SET company_id = ? WHERE company_id IS NULL", (default_company_id,))
    conn.execute("UPDATE export_jobs SET company_id = (SELECT company_id FROM quarterly_reports WHERE quarterly_reports.id = export_jobs.report_id) WHERE company_id IS NULL")


def add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_company_seed(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM companies WHERE slug = 'ef-windbrook'").fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO companies (name, slug, is_demo) VALUES (?, ?, 0)",
        ("EF / Windbrook", "ef-windbrook"),
    )
    return int(cur.lastrowid)


def migrate_users_role_constraint(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
    if not row or "CHECK(role IN ('admin'" not in row["sql"]:
        conn.execute("UPDATE users SET role = 'system_admin' WHERE role = 'admin'")
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            email TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users_new (id, email, full_name, role, password_hash, is_active, created_at, updated_at)
        SELECT id, email, full_name, CASE WHEN role = 'admin' THEN 'system_admin' ELSE role END, password_hash, is_active, created_at, updated_at
        FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        PRAGMA foreign_keys = ON;
        """
    )

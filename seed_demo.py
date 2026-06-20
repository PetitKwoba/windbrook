from __future__ import annotations

import json
import sqlite3
from typing import Any

from aw_portal.calculations import calculate_report
from aw_portal.db import connect, init_db
from aw_portal.web import audit, load_client_bundle, save_client, save_user, snapshot_from_form, store_report


DEMO_COMPANY = ("EF / Windbrook Demo", "ef-windbrook-demo")
DEMO_PASSWORD = "ChangeMe123!"


def seed_demo() -> dict[str, int]:
    with connect() as conn:
        init_db(conn)
        company_id = upsert_company(conn)
        users_created = seed_users(conn, company_id)
        clients = seed_clients(conn, company_id)
        reports_created = seed_reports(conn, company_id, clients)
        drafts_created = seed_draft(conn, company_id, clients[-1])
        events_created = seed_audit_events(conn, company_id)
        conn.commit()
    return {
        "companies": 1,
        "users_created": users_created,
        "clients": len(clients),
        "reports_created": reports_created,
        "drafts_created": drafts_created,
        "audit_events_created": events_created,
    }


def upsert_company(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM companies WHERE slug = ?", (DEMO_COMPANY[1],)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO companies (name, slug, is_demo) VALUES (?, ?, 1)",
        DEMO_COMPANY,
    )
    return int(cur.lastrowid)


def seed_users(conn: sqlite3.Connection, company_id: int) -> int:
    users = [
        ("system.admin@awportal.local", "System Admin", "system_admin", company_id),
        ("company.admin@ef-demo.local", "Company Admin", "company_admin", company_id),
        ("rebecca.demo@ef-demo.local", "Rebecca Demo", "planner", company_id),
        ("maryann.demo@ef-demo.local", "Maryann Demo", "assistant", company_id),
        ("andrew.demo@ef-demo.local", "Andrew Demo", "viewer", company_id),
    ]
    created = 0
    for email, full_name, role, user_company_id in users:
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            continue
        save_user(
            conn,
            {
                "email": email,
                "full_name": full_name,
                "role": role,
                "company_id": str(user_company_id),
                "is_active": "1",
                "password": DEMO_PASSWORD,
            },
        )
        created += 1
    return created


def seed_clients(conn: sqlite3.Connection, company_id: int) -> list[int]:
    client_forms = [
        {
            "household_name": "Demo - Walker Household",
            "household_type": "married",
            "monthly_salary": "18500",
            "monthly_expense_budget": "12200",
            "source_notes": "Demo data from manual PreciseFP transfer.",
            "private_reserve_notes": "Target includes home and auto deductibles.",
            "p1_first_name": "Alex",
            "p1_last_name": "Walker",
            "p1_dob": "1978-04-10",
            "p1_ssn_last4": "1234",
            "p2_first_name": "Jordan",
            "p2_last_name": "Walker",
            "p2_dob": "1980-09-16",
            "p2_ssn_last4": "5678",
            "deductible_1_label": "Home",
            "deductible_1_amount": "2500",
            "deductible_2_label": "Auto",
            "deductible_2_amount": "1000",
            "account_1_category": "retirement",
            "account_1_owner_index": "1",
            "account_1_account_type": "Traditional IRA",
            "account_1_institution": "Schwab",
            "account_1_account_last4": "1111",
            "account_2_category": "retirement",
            "account_2_owner_index": "2",
            "account_2_account_type": "401(k)",
            "account_2_institution": "Schwab",
            "account_2_account_last4": "2222",
            "account_3_category": "non_retirement",
            "account_3_owner_index": "0",
            "account_3_account_type": "Joint Brokerage",
            "account_3_institution": "Schwab",
            "account_3_account_last4": "3333",
            "trust_1_description": "Primary Residence",
            "trust_1_property_address": "100 Peachtree St, Atlanta, GA",
            "liability_1_liability_type": "Mortgage",
            "liability_1_lender": "Pinnacle Bank",
            "liability_1_interest_rate": "5.75",
            "liability_1_account_last4": "4444",
        },
        {
            "household_name": "Demo - Chen Family",
            "household_type": "married",
            "monthly_salary": "22000",
            "monthly_expense_budget": "14800",
            "source_notes": "Demo quarterly-prep household.",
            "p1_first_name": "Mia",
            "p1_last_name": "Chen",
            "p1_dob": "1985-01-24",
            "p1_ssn_last4": "2211",
            "p2_first_name": "Evan",
            "p2_last_name": "Chen",
            "p2_dob": "1983-11-08",
            "p2_ssn_last4": "8822",
            "deductible_1_label": "Umbrella",
            "deductible_1_amount": "5000",
            "account_1_category": "retirement",
            "account_1_owner_index": "1",
            "account_1_account_type": "Roth IRA",
            "account_1_institution": "Schwab",
            "account_2_category": "retirement",
            "account_2_owner_index": "2",
            "account_2_account_type": "SEP IRA",
            "account_2_institution": "Schwab",
            "account_3_category": "non_retirement",
            "account_3_owner_index": "0",
            "account_3_account_type": "Taxable Brokerage",
            "account_3_institution": "Schwab",
        },
        {
            "household_name": "Demo - Rivera Trust",
            "household_type": "trust",
            "monthly_salary": "9600",
            "monthly_expense_budget": "7200",
            "private_reserve_target_override": "50000",
            "source_notes": "Demo trust-heavy client.",
            "p1_first_name": "Sofia",
            "p1_last_name": "Rivera",
            "p1_dob": "1969-03-03",
            "p1_ssn_last4": "9033",
            "deductible_1_label": "Property",
            "deductible_1_amount": "3500",
            "account_1_category": "retirement",
            "account_1_owner_index": "1",
            "account_1_account_type": "IRA",
            "account_1_institution": "Schwab",
            "account_2_category": "non_retirement",
            "account_2_owner_index": "1",
            "account_2_account_type": "Investment Account",
            "account_2_institution": "Schwab",
            "trust_1_description": "Lake House",
            "trust_1_property_address": "42 Lake Ridge Dr, Blue Ridge, GA",
            "trust_2_description": "Rental Property",
            "trust_2_property_address": "18 Market St, Savannah, GA",
            "liability_1_liability_type": "HELOC",
            "liability_1_lender": "Pinnacle Bank",
            "liability_1_interest_rate": "7.10",
        },
        {
            "household_name": "Demo - Incomplete Household",
            "household_type": "single",
            "monthly_salary": "12500",
            "monthly_expense_budget": "8300",
            "source_notes": "Demo incomplete profile for readiness testing.",
            "p1_first_name": "Taylor",
            "p1_last_name": "Morgan",
            "p1_dob": "1990-12-12",
            "p1_ssn_last4": "7788",
            "deductible_1_label": "Auto",
            "deductible_1_amount": "1000",
        },
    ]
    client_ids = []
    for form in client_forms:
        row = conn.execute(
            "SELECT id FROM clients WHERE company_id = ? AND household_name = ?",
            (company_id, form["household_name"]),
        ).fetchone()
        if row:
            client_ids.append(int(row["id"]))
            continue
        client_id = save_client(conn, form, company_id=company_id)
        client_ids.append(client_id)
    return client_ids


def seed_reports(conn: sqlite3.Connection, company_id: int, client_ids: list[int]) -> int:
    report_specs = [
        (client_ids[0], "2026-03-31", 0.94),
        (client_ids[0], "2026-06-30", 1.0),
        (client_ids[1], "2026-06-30", 1.0),
        (client_ids[2], "2026-06-30", 1.0),
    ]
    created = 0
    for client_id, report_date, factor in report_specs:
        exists = conn.execute(
            "SELECT id FROM quarterly_reports WHERE company_id = ? AND client_id = ? AND report_date = ?",
            (company_id, client_id, report_date),
        ).fetchone()
        if exists:
            continue
        bundle = load_client_bundle(conn, client_id)
        form = build_report_form(bundle, report_date, factor)
        snapshot = snapshot_from_form(bundle, form)
        calculate_report(snapshot)
        store_report(conn, client_id, snapshot, company_id=company_id)
        created += 1
    return created


def build_report_form(bundle: dict[str, Any], report_date: str, factor: float) -> dict[str, str]:
    client = bundle["client"]
    data = {
        "report_date": report_date,
        "inflow": str(round(float(client["monthly_salary"]) * factor, 2)),
        "outflow": str(round(float(client["monthly_expense_budget"]) * factor, 2)),
        "private_reserve_balance": str(round(float(client["monthly_expense_budget"]) * 4.5 * factor, 2)),
        "investment_account_balance": "0",
    }
    for index, account in enumerate(bundle["accounts"], start=1):
        base = 95000 + index * 42000
        if account["category"] == "non_retirement":
            base = 155000 + index * 38000
        data[f"account_{account['id']}_balance"] = str(round(base * factor, 2))
        data[f"account_{account['id']}_cash_balance"] = str(round(base * 0.04 * factor, 2))
    for index, trust in enumerate(bundle["trust_assets"], start=1):
        data[f"trust_{trust['id']}_value"] = str(round((475000 + index * 125000) * factor, 2))
    for index, liability in enumerate(bundle["liabilities"], start=1):
        data[f"liability_{liability['id']}_balance"] = str(round((210000 - index * 18000) * factor, 2))
    return data


def seed_draft(conn: sqlite3.Connection, company_id: int, client_id: int) -> int:
    exists = conn.execute(
        "SELECT id FROM report_drafts WHERE company_id = ? AND client_id = ? AND report_date = ?",
        (company_id, client_id, "2026-09-30"),
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        "INSERT INTO report_drafts (company_id, client_id, report_date, data_json, status) VALUES (?, ?, ?, ?, 'draft')",
        (
            company_id,
            client_id,
            "2026-09-30",
            json.dumps({"report_date": "2026-09-30", "inflow": "12500", "outflow": ""}, sort_keys=True),
        ),
    )
    return 1


def seed_audit_events(conn: sqlite3.Connection, company_id: int) -> int:
    events: list[tuple[str, str, str, dict[str, Any]]] = [
        ("login", "user", "demo", {"email": "company.admin@ef-demo.local"}),
        ("client_created", "client", "demo", {"household": "Demo - Walker Household"}),
        ("report_draft_saved", "client", "demo", {"household": "Demo - Incomplete Household"}),
        ("report_generated", "report", "demo", {"household": "Demo - Walker Household"}),
        ("pdf_downloaded", "report", "demo", {"file": "sacs.pdf"}),
    ]
    created = 0
    for action, entity_type, entity_id, metadata in events:
        if conn.execute(
            "SELECT id FROM audit_events WHERE company_id = ? AND action = ? AND entity_type = ? AND entity_id = ?",
            (company_id, action, entity_type, entity_id),
        ).fetchone():
            continue
        audit(conn, None, action, entity_type, entity_id, metadata, company_id=company_id)
        created += 1
    return created


if __name__ == "__main__":
    result = seed_demo()
    print("Seed complete:", result)

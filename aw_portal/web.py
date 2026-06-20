from __future__ import annotations

import html
import json
import os
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .calculations import age_on, calculate_report, format_money, parse_money, parse_rate
from .db import connect, init_db
from .excel import build_excel_workbook
from .pdfs import build_sacs_pdf, build_tcc_pdf
from .security import (
    SESSION_COOKIE,
    can,
    default_admin_credentials,
    hash_password,
    is_company_admin,
    is_system_admin,
    mask_ssn_last4,
    new_csrf_token,
    new_session_id,
    parse_cookies,
    session_expires_at,
    verify_password,
)


MAX_ACCOUNTS = 18
MAX_LIABILITIES = 3
MAX_TRUSTS = 2
MAX_DEDUCTIBLES = 8
MAX_REPORT_ONLY_ACCOUNTS = 4
MAX_REPORT_ONLY_TRUSTS = 2
MAX_REPORT_ONLY_LIABILITIES = 3
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "aw_portal" / "static"


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    with connect() as conn:
        init_db(conn)
        ensure_default_admin(conn)
    server = ThreadingHTTPServer((host, port), PortalHandler)
    try:
        print(f"AW Client Report Portal running at http://{host}:{port}", flush=True)
    except Exception:
        pass
    server.serve_forever()


def ensure_default_admin(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    if count:
        return
    company_id = ensure_default_company(conn)
    email, name, password = default_admin_credentials()
    conn.execute(
        "INSERT INTO users (company_id, email, full_name, role, password_hash) VALUES (?, ?, ?, 'system_admin', ?)",
        (company_id, email.lower(), name, hash_password(password)),
    )
    conn.execute(
        "INSERT INTO audit_events (company_id, action, entity_type, metadata_json) VALUES (?, ?, ?, ?)",
        (company_id, "bootstrap_system_admin", "user", json.dumps({"email": email.lower()})),
    )
    conn.commit()


def ensure_default_company(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM companies WHERE slug = 'ef-windbrook'").fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO companies (name, slug, is_demo) VALUES (?, ?, 0)", ("EF / Windbrook", "ef-windbrook"))
    return int(cur.lastrowid)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def badge(text: str, kind: str = "neutral") -> str:
    return f"<span class='badge {esc(kind)}'>{esc(text)}</span>"


def page(title: str, body: str, *, user: dict[str, Any] | None = None, csrf: str = "") -> bytes:
    user_nav = ""
    role = user.get("role") if user else ""
    if user:
        admin_link = "<a href='/users'>Users</a>" if is_company_admin(role) else ""
        company_link = "<a href='/companies'>Companies</a>" if is_system_admin(role) else ""
        user_nav = f"""
        <nav class="side-nav">
          <a class="brand" href="/clients"><span>AW</span><strong>Report Portal</strong></a>
          <a href="/clients">Clients</a>
          <a href="/reports">Reports</a>
          <a href="/audit">Activity</a>
          {admin_link}
          {company_link}
          <form method="post" action="/logout" class="logout">
            <input type="hidden" name="csrf_token" value="{esc(csrf)}">
            <button type="submit" class="link-button">Logout</button>
          </form>
          <div class="user-chip">{esc(user['full_name'])}<span>{esc(role_label(role))} · {esc(user.get('company_name') or 'All Companies')}</span></div>
        </nav>
        """
    shell_class = "app-shell" if user else "login-shell"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} - AW Portal</title>
  <link rel="stylesheet" href="/static/app.css">
  <script defer src="/static/app.js"></script>
</head>
<body>
  <div class="{shell_class}">
    {user_nav}
    <main class="main-panel">{body}</main>
  </div>
</body>
</html>""".encode("utf-8")


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "AWPortal/2.0"

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def route(self, method: str) -> None:
        self.parsed = urlparse(self.path)
        self.parts = [part for part in self.parsed.path.split("/") if part]
        self._form_cache: dict[str, str] | None = None
        try:
            if self.serve_public(method):
                return
            self.user, self.csrf_token = self.load_user()
            if not self.user:
                self.redirect("/login")
                return
            if method == "POST":
                self.require_csrf()
            self.route_authenticated(method)
        except PermissionError as exc:
            self.respond(app_page("Forbidden", f"<h1>Forbidden</h1><p class='notice error'>{esc(exc)}</p>", self), HTTPStatus.FORBIDDEN)
        except (ValueError, sqlite3.Error, KeyError) as exc:
            self.respond(app_page("Error", f"<h1>Something went wrong</h1><p class='notice error'>{esc(exc)}</p><p><a href='/clients'>Back to clients</a></p>", self), HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_public(self, method: str) -> bool:
        if self.parsed.path == "/health":
            self.respond(b'{"ok":true}', content_type="application/json")
            return True
        if self.parts[:1] == ["static"] and method == "GET":
            self.render_static(self.parts[1:])
            return True
        if method == "GET" and self.parts == ["login"]:
            self.render_login()
            return True
        if method == "POST" and self.parts == ["login"]:
            self.login()
            return True
        return False

    def route_authenticated(self, method: str) -> None:
        p = self.parts
        if self.parsed.path == "/":
            self.redirect("/clients")
        elif method == "GET" and p == ["clients"]:
            self.render_clients()
        elif method == "GET" and p == ["clients", "new"]:
            self.require_role("planner")
            self.render_client_form()
        elif method == "POST" and p == ["clients"]:
            self.require_role("planner")
            self.create_client()
        elif len(p) == 2 and p[0] == "clients" and method == "GET":
            if parse_qs(self.parsed.query).get("edit") == ["1"]:
                self.require_role("planner")
                with connect() as conn:
                    init_db(conn)
                    ensure_client_access(conn, self.user, int(p[1]))
                    self.render_client_form(load_client_bundle(conn, int(p[1])))
            else:
                self.render_client_detail(int(p[1]))
        elif len(p) == 2 and p[0] == "clients" and method == "POST":
            self.require_role("planner")
            self.update_client(int(p[1]))
        elif len(p) == 4 and p[0] == "clients" and p[2] == "reports" and p[3] == "new" and method == "GET":
            self.require_role("assistant")
            self.render_report_form(int(p[1]))
        elif len(p) == 4 and p[0] == "clients" and p[2] == "reports" and p[3] == "draft" and method == "POST":
            self.require_role("assistant")
            self.save_report_draft(int(p[1]))
        elif len(p) == 3 and p[0] == "clients" and p[2] == "reports" and method == "POST":
            self.require_role("assistant")
            self.create_report(int(p[1]))
        elif method == "GET" and p == ["reports"]:
            self.render_reports()
        elif len(p) == 2 and p[0] == "reports" and method == "GET":
            self.render_report(int(p[1]))
        elif len(p) == 3 and p[0] == "reports" and p[2] in {"sacs.pdf", "tcc.pdf", "excel.xlsx"} and method == "GET":
            self.render_report_download(int(p[1]), p[2])
        elif method == "GET" and p == ["companies"]:
            self.require_system_admin()
            self.render_companies()
        elif method == "GET" and p == ["users"]:
            self.require_company_admin()
            self.render_users()
        elif method == "GET" and p == ["users", "new"]:
            self.require_company_admin()
            self.render_user_form()
        elif method == "POST" and p == ["users"]:
            self.require_company_admin()
            self.create_user()
        elif len(p) == 2 and p[0] == "users" and method == "GET":
            self.require_company_admin()
            self.render_user_form(int(p[1]))
        elif len(p) == 2 and p[0] == "users" and method == "POST":
            self.require_company_admin()
            self.update_user(int(p[1]))
        elif method == "GET" and p == ["audit"]:
            self.render_audit()
        elif method == "POST" and p == ["logout"]:
            self.logout()
        else:
            self.not_found()

    def form(self) -> dict[str, str]:
        if self._form_cache is not None:
            return self._form_cache
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw, keep_blank_values=True)
        self._form_cache = {key: values[-1] for key, values in parsed.items()}
        return self._form_cache

    def load_user(self) -> tuple[dict[str, Any] | None, str]:
        session_id = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE)
        if not session_id:
            return None, ""
        with connect() as conn:
            init_db(conn)
            row = conn.execute(
                """
                SELECT s.id AS session_id, s.csrf_token, s.expires_at, u.*, c.name AS company_name
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN companies c ON c.id = u.company_id
                WHERE s.id = ? AND u.is_active = 1
                """,
                (session_id,),
            ).fetchone()
            if row is None or int(row["expires_at"]) < int(time.time()):
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                return None, ""
            return dict(row), row["csrf_token"]

    def require_csrf(self) -> None:
        token = self.form().get("csrf_token", "")
        if not token or token != self.csrf_token:
            raise PermissionError("Invalid or missing CSRF token.")

    def require_role(self, role: str) -> None:
        if not can(self.user.get("role"), role):
            raise PermissionError(f"{role.title()} access is required.")

    def require_company_admin(self) -> None:
        if not is_company_admin(self.user.get("role")):
            raise PermissionError("Company admin access is required.")

    def require_system_admin(self) -> None:
        if not is_system_admin(self.user.get("role")):
            raise PermissionError("System admin access is required.")

    def respond(self, body: bytes, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def set_session_cookie(self, session_id: str) -> None:
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}={session_id}; HttpOnly; SameSite=Lax; Path=/")

    def clear_session_cookie(self) -> None:
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")

    def render_static(self, parts: list[str]) -> None:
        if not parts:
            self.not_found()
            return
        name = parts[-1]
        if name not in {"app.css", "app.js"}:
            self.not_found()
            return
        path = STATIC_DIR / name
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8"
        self.respond(path.read_bytes(), content_type=content_type)

    def render_login(self, error: str = "") -> None:
        admin_email, _, admin_password = default_admin_credentials()
        body = f"""
        <section class="login-frame">
          <div class="login-copy">
            <div class="login-mark">AW</div>
            <p class="eyebrow">EF / Windbrook</p>
            <h1>Client Report Portal</h1>
            <p>Secure quarterly reporting for SACS cashflow, TCC net worth, and internal Excel review workbooks.</p>
          </div>
          <form method="post" action="/login" class="login-card stack">
            <div>
              <p class="eyebrow">Secure Login</p>
              <h2>Sign in</h2>
            </div>
            {"<p class='notice error'>" + esc(error) + "</p>" if error else ""}
            {field("Email", "email", admin_email, input_type="email", required=True)}
            {field("Password", "password", admin_password, input_type="password", required=True)}
            <button type="submit">Sign in</button>
            <p class="mini">Local development credentials only. Configure production credentials before first deployment.</p>
          </form>
        </section>
        """
        self.respond(page("Login", body))

    def login(self) -> None:
        data = self.form()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        with connect() as conn:
            init_db(conn)
            ensure_default_admin(conn)
            user = conn.execute("SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)).fetchone()
            if user is None or not verify_password(password, user["password_hash"]):
                audit(conn, None, "login_failed", "user", "", {"email": email})
                self.render_login("Invalid email or password.")
                return
            session_id = new_session_id()
            csrf = new_csrf_token()
            conn.execute(
                "INSERT INTO sessions (id, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (session_id, user["id"], csrf, session_expires_at()),
            )
            audit(conn, user["id"], "login", "user", str(user["id"]), {}, company_id=user["company_id"])
            conn.commit()
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/clients")
        self.set_session_cookie(session_id)
        self.end_headers()

    def logout(self) -> None:
        session_id = parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE)
        with connect() as conn:
            init_db(conn)
            if session_id:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            audit(conn, self.user["id"], "logout", "user", str(self.user["id"]), {}, company_id=self.user["company_id"])
            conn.commit()
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/login")
        self.clear_session_cookie()
        self.end_headers()

    def not_found(self) -> None:
        self.respond(app_page("Not found", "<h1>Not found</h1><p>The requested page does not exist.</p>", self), HTTPStatus.NOT_FOUND)

    def render_clients(self) -> None:
        query = parse_qs(self.parsed.query).get("q", [""])[0].strip()
        with connect() as conn:
            init_db(conn)
            tenant_sql, tenant_params = tenant_where(self.user, "c")
            rows = conn.execute(
                f"""
                SELECT c.*,
                       MAX(q.report_date) AS last_report_date,
                       COUNT(DISTINCT q.id) AS report_count,
                       MIN(p.first_name || ' ' || p.last_name) AS primary_contact
                FROM clients c
                LEFT JOIN quarterly_reports q ON q.client_id = c.id
                LEFT JOIN client_people p ON p.client_id = c.id AND p.person_index = 1
                WHERE c.archive_status = 'active'
                  {tenant_sql}
                  AND (? = '' OR c.household_name LIKE '%' || ? || '%')
                GROUP BY c.id
                ORDER BY c.household_name
                """,
                (*tenant_params, query, query),
            ).fetchall()
        body_rows = "".join(client_row(row) for row in rows) or "<tr><td colspan='7'>No active clients match this view.</td></tr>"
        actions = "<a class='button' href='/clients/new'>New Client</a>" if can(self.user["role"], "planner") else ""
        body = f"""
        <div class="page-header">
          <div><p class="eyebrow">Dashboard</p><h1>Clients</h1><p>Prepare quarterly reports from a single operational workspace.</p></div>
          <div class="toolbar">{actions}</div>
        </div>
        <form method="get" class="filter-bar">
          <input name="q" value="{esc(query)}" placeholder="Filter by household">
          <button type="submit" class="secondary">Filter</button>
        </form>
        <div class="table-wrap">
          <table data-sortable>
            <thead><tr><th>Household</th><th>Primary Contact</th><th>Status</th><th>Profile</th><th>Last Report</th><th>Reports</th><th>Budget</th></tr></thead>
            <tbody>{body_rows}</tbody>
          </table>
        </div>
        """
        self.respond(app_page("Clients", body, self))

    def render_client_form(self, bundle: dict[str, Any] | None = None, error: str = "") -> None:
        bundle = bundle or empty_bundle()
        client = bundle["client"]
        action = "/clients" if not client.get("id") else f"/clients/{client['id']}"
        title = "New Client" if not client.get("id") else f"Edit {client['household_name']}"
        body = f"""
        <div class="page-header"><div><p class="eyebrow">Client Profile</p><h1>{esc(title)}</h1><p>Static profile data used to prefill quarterly report prep.</p></div></div>
        {error_html(error)}
        <form method="post" action="{action}" class="stack">
          {csrf_input(self)}
          <section class="panel">
            <h2>Household</h2>
            <div class="grid">
              {field('Household Name', 'household_name', client.get('household_name'), required=True)}
              {select_field('Household Type', 'household_type', [('single','Single'),('married','Married'),('trust','Trust / Entity')], client.get('household_type', 'married'))}
              {field('Monthly Salary / Inflow', 'monthly_salary', client.get('monthly_salary'), inputmode='decimal', required=True)}
              {field('Monthly Expense Budget / Outflow', 'monthly_expense_budget', client.get('monthly_expense_budget'), inputmode='decimal', required=True)}
              {field('Private Reserve Target Override', 'private_reserve_target_override', client.get('private_reserve_target_override'), inputmode='decimal')}
              {select_field('Archive Status', 'archive_status', [('active','Active'),('archived','Archived')], client.get('archive_status', 'active'))}
            </div>
            <div class="grid two">
              {field('Source Notes', 'source_notes', client.get('source_notes'))}
              {field('Private Reserve Notes', 'private_reserve_notes', client.get('private_reserve_notes'))}
            </div>
          </section>
          {people_fields(bundle, reveal=can(self.user['role'], 'planner'))}
          {deductible_fields(bundle)}
          {account_fields(bundle)}
          {trust_fields(bundle)}
          {liability_fields(bundle)}
          <div class="toolbar sticky-actions">
            <button type="submit">Save Client</button>
            <a class="button secondary" href="/clients">Cancel</a>
          </div>
        </form>
        """
        self.respond(app_page(title, body, self))

    def create_client(self) -> None:
        data = self.form()
        with connect() as conn:
            init_db(conn)
            client_id = save_client(conn, data, company_id=self.user["company_id"])
            audit(conn, self.user["id"], "client_created", "client", str(client_id), {"household": data.get("household_name")}, company_id=self.user["company_id"])
            conn.commit()
        self.redirect(f"/clients/{client_id}")

    def update_client(self, client_id: int) -> None:
        data = self.form()
        with connect() as conn:
            init_db(conn)
            ensure_client_access(conn, self.user, client_id)
            save_client(conn, data, client_id, company_id=self.user["company_id"])
            audit(conn, self.user["id"], "client_updated", "client", str(client_id), {}, company_id=self.user["company_id"])
            conn.commit()
        self.redirect(f"/clients/{client_id}")

    def render_client_detail(self, client_id: int) -> None:
        with connect() as conn:
            init_db(conn)
            ensure_client_access(conn, self.user, client_id)
            bundle = load_client_bundle(conn, client_id)
            reports = conn.execute("SELECT * FROM quarterly_reports WHERE client_id = ? ORDER BY report_date DESC, id DESC", (client_id,)).fetchall()
            drafts = conn.execute("SELECT * FROM report_drafts WHERE client_id = ? ORDER BY updated_at DESC LIMIT 5", (client_id,)).fetchall()
            events = conn.execute("SELECT * FROM audit_events WHERE entity_type = 'client' AND entity_id = ? ORDER BY id DESC LIMIT 8", (str(client_id),)).fetchall()
        client = bundle["client"]
        people = people_summary(bundle, reveal=can(self.user["role"], "planner"))
        profile_score = profile_completeness(bundle)
        actions = ""
        if can(self.user["role"], "assistant"):
            actions += f"<a class='button' href='/clients/{client_id}/reports/new'>Start Quarterly Report</a>"
        if can(self.user["role"], "planner"):
            actions += f"<a class='button secondary' href='/clients/{client_id}?edit=1'>Edit Profile</a>"
        body = f"""
        <div class="page-header">
          <div><p class="eyebrow">Client</p><h1>{esc(client['household_name'])}</h1><p>{esc(people)}</p></div>
          <div class="toolbar">{actions}<a class="button secondary" href="/clients">All Clients</a></div>
        </div>
        <div class="summary-grid">
          {metric('Profile Completeness', f'{profile_score}%', 'ready' if profile_score >= 85 else 'warn')}
          {metric('Monthly Inflow', format_money(client['monthly_salary']))}
          {metric('Expense Budget', format_money(client['monthly_expense_budget']))}
          {metric('Reports', str(len(reports)))}
        </div>
        {client_tables(bundle)}
        <section class="panel"><h2>Report Drafts</h2>{draft_table(drafts)}</section>
        <section class="panel"><h2>Report History</h2>{report_table(reports)}</section>
        <section class="panel"><h2>Activity</h2>{audit_table(events)}</section>
        """
        self.respond(app_page(client["household_name"], body, self))

    def render_report_form(self, client_id: int, error: str = "") -> None:
        with connect() as conn:
            init_db(conn)
            ensure_client_access(conn, self.user, client_id)
            bundle = load_client_bundle(conn, client_id)
            last = latest_values(conn, client_id)
            draft = conn.execute("SELECT * FROM report_drafts WHERE client_id = ? ORDER BY updated_at DESC LIMIT 1", (client_id,)).fetchone()
        client = bundle["client"]
        draft_data = json.loads(draft["data_json"]) if draft else {}
        body = f"""
        <div class="page-header">
          <div><p class="eyebrow">Quarterly Report</p><h1>{esc(client['household_name'])}</h1><p>Complete each section, compare prior values, then generate immutable PDF and Excel reports.</p></div>
        </div>
        {error_html(error)}
        <form method="post" action="/clients/{client_id}/reports" class="stack" data-report-form>
          {csrf_input(self)}
          <section class="panel checklist">
            <h2>SACS Inputs</h2>
            <div class="grid">
              {last_field('Report Date', 'report_date', draft_data.get('report_date', ''), last, input_type='date', required=True)}
              {last_field('Inflow', 'inflow', draft_data.get('inflow') or client['monthly_salary'], last, required=True, calc='inflow')}
              {last_field('Outflow', 'outflow', draft_data.get('outflow') or client['monthly_expense_budget'], last, required=True, calc='outflow')}
              {last_field('Private Reserve Balance', 'private_reserve_balance', draft_data.get('private_reserve_balance', ''), last, required=True)}
              {last_field('Investment Account Balance', 'investment_account_balance', draft_data.get('investment_account_balance', ''), last)}
            </div>
          </section>
          <section class="panel checklist">
            <h2>Deductibles</h2>
            {report_deductible_fields(bundle, draft_data)}
          </section>
          <section class="panel checklist">
            <h2>Retirement and Non-Retirement Assets</h2>
            {report_account_fields(bundle, last, draft_data)}
            {report_only_account_fields(draft_data)}
          </section>
          <section class="panel checklist">
            <h2>Trust Assets</h2>
            {report_trust_fields(bundle, last, draft_data)}
            {report_only_trust_fields(draft_data)}
          </section>
          <section class="panel checklist">
            <h2>Liabilities</h2>
            {report_liability_fields(bundle, last, draft_data)}
            {report_only_liability_fields(draft_data)}
          </section>
          <section class="panel">
            <h2>Live Review</h2>
            <div class="summary-grid">
              {metric('Monthly Excess', '$0', 'neutral', 'live-excess')}
              {metric('Private Reserve Target', '$0', 'neutral', 'live-target')}
            </div>
          </section>
          <div class="toolbar sticky-actions">
            <button type="button" class="secondary" data-use-last>Use Last Values Where Blank</button>
            <button type="submit" formaction="/clients/{client_id}/reports/draft" class="secondary">Save Draft</button>
            <button type="submit">Generate Report</button>
            <a class="button secondary" href="/clients/{client_id}">Cancel</a>
          </div>
        </form>
        """
        self.respond(app_page("Quarterly Report", body, self))

    def save_report_draft(self, client_id: int) -> None:
        data = self.form()
        with connect() as conn:
            init_db(conn)
            ensure_client_access(conn, self.user, client_id)
            conn.execute(
                "INSERT INTO report_drafts (company_id, client_id, user_id, report_date, data_json) VALUES (?, ?, ?, ?, ?)",
                (self.user["company_id"], client_id, self.user["id"], data.get("report_date", ""), json.dumps(strip_security_fields(data), sort_keys=True)),
            )
            audit(conn, self.user["id"], "report_draft_saved", "client", str(client_id), {}, company_id=self.user["company_id"])
            conn.commit()
        self.redirect(f"/clients/{client_id}/reports/new")

    def create_report(self, client_id: int) -> None:
        data = self.form()
        try:
            with connect() as conn:
                init_db(conn)
                ensure_client_access(conn, self.user, client_id)
                bundle = load_client_bundle(conn, client_id)
                snapshot = snapshot_from_form(bundle, data)
                calculate_report(snapshot)
                persist_report_only_profile_items(conn, client_id, snapshot)
                report_id = store_report(conn, client_id, snapshot, user_id=self.user["id"], company_id=self.user["company_id"])
                conn.execute("UPDATE report_drafts SET status = 'generated' WHERE client_id = ? AND status = 'draft'", (client_id,))
                audit(conn, self.user["id"], "report_generated", "report", str(report_id), {"client_id": client_id}, company_id=self.user["company_id"])
                conn.commit()
        except ValueError as exc:
            self.render_report_form(client_id, str(exc))
            return
        self.redirect(f"/reports/{report_id}")

    def render_reports(self) -> None:
        with connect() as conn:
            init_db(conn)
            tenant_sql, tenant_params = tenant_where(self.user, "q")
            reports = conn.execute(
                f"""
                SELECT q.*, c.household_name
                FROM quarterly_reports q
                JOIN clients c ON c.id = q.client_id
                WHERE 1 = 1 {tenant_sql}
                ORDER BY q.report_date DESC, q.id DESC
                """
                ,
                tenant_params,
            ).fetchall()
        self.respond(app_page("Reports", f"<div class='page-header'><div><p class='eyebrow'>Reports</p><h1>Report History</h1></div></div>{report_table(reports, include_client=True)}", self))

    def render_companies(self) -> None:
        with connect() as conn:
            init_db(conn)
            companies = conn.execute(
                """
                SELECT c.*,
                       COUNT(DISTINCT u.id) AS user_count,
                       COUNT(DISTINCT cl.id) AS client_count,
                       COUNT(DISTINCT q.id) AS report_count
                FROM companies c
                LEFT JOIN users u ON u.company_id = c.id
                LEFT JOIN clients cl ON cl.company_id = c.id
                LEFT JOIN quarterly_reports q ON q.company_id = c.id
                GROUP BY c.id
                ORDER BY c.name
                """
            ).fetchall()
        rows = "".join(
            f"<tr><td>{esc(c['name'])}</td><td>{esc(c['slug'])}</td><td>{badge('Demo' if c['is_demo'] else 'Production', 'warn' if c['is_demo'] else 'ready')}</td><td>{c['user_count']}</td><td>{c['client_count']}</td><td>{c['report_count']}</td></tr>"
            for c in companies
        ) or "<tr><td colspan='6'>No companies configured.</td></tr>"
        body = f"""
        <div class='page-header'>
          <div><p class='eyebrow'>System Admin</p><h1>Companies</h1><p>Platform-level tenant visibility for EF/Windbrook and demo environments.</p></div>
        </div>
        <table><thead><tr><th>Company</th><th>Slug</th><th>Type</th><th>Users</th><th>Clients</th><th>Reports</th></tr></thead><tbody>{rows}</tbody></table>
        """
        self.respond(app_page("Companies", body, self))

    def render_report(self, report_id: int) -> None:
        with connect() as conn:
            init_db(conn)
            report = load_report(conn, report_id)
            ensure_report_access(conn, self.user, report_id)
        snapshot = json.loads(report["snapshot_json"])
        totals = snapshot["totals"]
        body = f"""
        <div class="page-header">
          <div><p class="eyebrow">Generated Report</p><h1>{esc(snapshot['client']['household_name'])}</h1><p>Report date: {esc(snapshot['report_date'])}. This is an immutable quarterly snapshot.</p></div>
          <div class="toolbar">
            <a class="button" href="/reports/{report_id}/sacs.pdf">Download SACS PDF</a>
            <a class="button" href="/reports/{report_id}/tcc.pdf">Download TCC PDF</a>
            <a class="button secondary" href="/reports/{report_id}/excel.xlsx">Download Excel</a>
            {badge(report['export_status'], 'neutral')}
          </div>
        </div>
        <div class="cashflow-strip">
          <div class="flow-pill green"><span>Inflow</span><strong>{esc(format_money(totals['inflow']))}</strong></div>
          <span class="flow-arrow">-&gt;</span>
          <div class="flow-pill red"><span>Outflow</span><strong>{esc(format_money(totals['outflow']))}</strong></div>
          <span class="flow-arrow">-&gt;</span>
          <div class="flow-pill blue"><span>Private Reserve Excess</span><strong>{esc(format_money(totals['excess']))}</strong></div>
        </div>
        <div class="summary-grid">
          {metric('Client 1 Retirement', format_money(totals['client_1_retirement']))}
          {metric('Client 2 Retirement', format_money(totals['client_2_retirement']))}
          {metric('Non-Retirement', format_money(totals['non_retirement']))}
          {metric('Trust', format_money(totals['trust']))}
          {metric('Grand Total', format_money(totals['grand_total']), 'ready')}
          {metric('Liabilities Separate', format_money(totals['liabilities']), 'warn')}
          {metric('Private Reserve Target', format_money(totals['private_reserve_target']))}
        </div>
        {snapshot_tables(snapshot)}
        """
        self.respond(app_page("Report", body, self))

    def render_report_download(self, report_id: int, file_name: str) -> None:
        with connect() as conn:
            init_db(conn)
            report = load_report(conn, report_id)
            ensure_report_access(conn, self.user, report_id)
            audit_action = "excel_downloaded" if file_name == "excel.xlsx" else "pdf_downloaded"
            audit(conn, self.user["id"], audit_action, "report", str(report_id), {"file": file_name}, company_id=report["company_id"])
            conn.commit()
        snapshot = json.loads(report["snapshot_json"])
        if file_name == "sacs.pdf":
            body = build_sacs_pdf(snapshot)
            download_name = f"{safe_filename(snapshot['client']['household_name'])}-SACS-{snapshot['report_date']}.pdf"
            content_type = "application/pdf"
        elif file_name == "tcc.pdf":
            body = build_tcc_pdf(snapshot)
            download_name = f"{safe_filename(snapshot['client']['household_name'])}-TCC-{snapshot['report_date']}.pdf"
            content_type = "application/pdf"
        else:
            body = build_excel_workbook(snapshot, dict(report))
            download_name = f"{safe_filename(snapshot['client']['household_name'])}-Report-{snapshot['report_date']}.xlsx"
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def render_users(self) -> None:
        with connect() as conn:
            init_db(conn)
            tenant_sql, tenant_params = tenant_where(self.user, "u")
            users = conn.execute(
                f"""
                SELECT u.*, c.name AS company_name
                FROM users u
                LEFT JOIN companies c ON c.id = u.company_id
                WHERE 1 = 1 {tenant_sql}
                ORDER BY u.is_active DESC, c.name, u.full_name
                """,
                tenant_params,
            ).fetchall()
        rows = "".join(
            f"<tr><td><a href='/users/{u['id']}'>{esc(u['full_name'])}</a></td><td>{esc(u['email'])}</td><td>{esc(u['company_name'] or '-')}</td><td>{badge(role_label(u['role']), 'ready' if is_company_admin(u['role']) else 'neutral')}</td><td>{badge('Active' if u['is_active'] else 'Inactive', 'ready' if u['is_active'] else 'warn')}</td></tr>"
            for u in users
        )
        body = f"<div class='page-header'><div><p class='eyebrow'>Admin</p><h1>Users</h1></div><div class='toolbar'><a class='button' href='/users/new'>New User</a></div></div><table><thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
        self.respond(app_page("Users", body, self))

    def render_user_form(self, user_id: int | None = None, error: str = "") -> None:
        user = {"email": "", "full_name": "", "role": "viewer", "is_active": 1, "company_id": self.user.get("company_id")}
        if user_id:
            with connect() as conn:
                init_db(conn)
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                if row is None:
                    raise ValueError("User not found")
                ensure_user_access(self.user, dict(row))
                user = dict(row)
        with connect() as conn:
            init_db(conn)
            companies = visible_companies(conn, self.user)
        role_options = role_options_for(self.user.get("role"))
        company_options = [(str(c["id"]), c["name"]) for c in companies]
        action = "/users" if user_id is None else f"/users/{user_id}"
        body = f"""
        <div class="page-header"><div><p class="eyebrow">Admin</p><h1>{'New User' if user_id is None else 'Edit User'}</h1></div></div>
        {error_html(error)}
        <form method="post" action="{action}" class="panel stack">
            {csrf_input(self)}
            <div class="grid">
                {field('Full Name', 'full_name', user.get('full_name'), required=True)}
                {field('Email', 'email', user.get('email'), input_type='email', required=True)}
            {select_field('Company', 'company_id', company_options, user.get('company_id'))}
            {select_field('Role', 'role', role_options, user.get('role'))}
            {select_field('Status', 'is_active', [('1','Active'),('0','Inactive')], str(user.get('is_active', 1)))}
            {field('Password', 'password', '', input_type='password', required=user_id is None)}
          </div>
          <div class="toolbar"><button type="submit">Save User</button><a class="button secondary" href="/users">Cancel</a></div>
        </form>
        """
        self.respond(app_page("User", body, self))

    def create_user(self) -> None:
        data = self.form()
        with connect() as conn:
            init_db(conn)
            user_id = save_user(conn, data, actor=self.user)
            audit(conn, self.user["id"], "user_created", "user", str(user_id), {"role": data.get("role")}, company_id=int(data.get("company_id") or self.user["company_id"]))
            conn.commit()
        self.redirect("/users")

    def update_user(self, user_id: int) -> None:
        data = self.form()
        with connect() as conn:
            init_db(conn)
            target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise ValueError("User not found")
            ensure_user_access(self.user, dict(target))
            save_user(conn, data, user_id, actor=self.user)
            audit(conn, self.user["id"], "user_updated", "user", str(user_id), {"role": data.get("role")}, company_id=int(data.get("company_id") or self.user["company_id"]))
            conn.commit()
        self.redirect("/users")

    def render_audit(self) -> None:
        with connect() as conn:
            init_db(conn)
            tenant_sql, tenant_params = tenant_where(self.user, "a")
            events = conn.execute(
                f"""
                SELECT a.*, u.full_name
                FROM audit_events a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE 1 = 1 {tenant_sql}
                ORDER BY a.id DESC LIMIT 100
                """,
                tenant_params,
            ).fetchall()
        body = f"<div class='page-header'><div><p class='eyebrow'>Activity</p><h1>Audit Log</h1></div></div>{audit_table(events, include_user=True)}"
        self.respond(app_page("Audit", body, self))


def app_page(title: str, body: str, handler: PortalHandler) -> bytes:
    return page(title, body, user=getattr(handler, "user", None), csrf=getattr(handler, "csrf_token", ""))


def csrf_input(handler: PortalHandler) -> str:
    return f"<input type='hidden' name='csrf_token' value='{esc(handler.csrf_token)}'>"


def error_html(error: str) -> str:
    return f"<p class='notice error'>{esc(error)}</p>" if error else ""


ROLE_LABELS = {
    "system_admin": "System Admin",
    "company_admin": "Company Admin",
    "planner": "Planner",
    "assistant": "Assistant",
    "viewer": "Viewer",
}


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", str(role or "").replace("_", " ").title())


def role_options_for(actor_role: str | None) -> list[tuple[str, str]]:
    if is_system_admin(actor_role):
        roles = ("system_admin", "company_admin", "planner", "assistant", "viewer")
    else:
        roles = ("planner", "assistant", "viewer")
    return [(role, ROLE_LABELS[role]) for role in roles]


def tenant_where(user: dict[str, Any], alias: str) -> tuple[str, tuple[Any, ...]]:
    if is_system_admin(user.get("role")):
        return "", ()
    company_id = user.get("company_id")
    if not company_id:
        raise PermissionError("User is not assigned to a company.")
    return f" AND {alias}.company_id = ?", (company_id,)


def visible_companies(conn: sqlite3.Connection, user: dict[str, Any]) -> list[sqlite3.Row]:
    if is_system_admin(user.get("role")):
        return list(conn.execute("SELECT * FROM companies ORDER BY name"))
    if not user.get("company_id"):
        return []
    return list(conn.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)))


def ensure_client_access(conn: sqlite3.Connection, user: dict[str, Any], client_id: int) -> None:
    if is_system_admin(user.get("role")):
        return
    row = conn.execute("SELECT company_id FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise ValueError("Client not found.")
    if int(row["company_id"]) != int(user.get("company_id") or 0):
        raise PermissionError("This client belongs to another company.")


def ensure_report_access(conn: sqlite3.Connection, user: dict[str, Any], report_id: int) -> None:
    if is_system_admin(user.get("role")):
        return
    row = conn.execute("SELECT company_id FROM quarterly_reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        raise ValueError("Report not found.")
    if int(row["company_id"]) != int(user.get("company_id") or 0):
        raise PermissionError("This report belongs to another company.")


def ensure_user_access(actor: dict[str, Any], target: dict[str, Any]) -> None:
    if is_system_admin(actor.get("role")):
        return
    if not is_company_admin(actor.get("role")):
        raise PermissionError("Company admin access is required.")
    if target.get("role") in {"system_admin", "company_admin"}:
        raise PermissionError("Only system admins can manage admin users.")
    if int(target.get("company_id") or 0) != int(actor.get("company_id") or 0):
        raise PermissionError("This user belongs to another company.")


def field(label: str, name: str, value: Any = "", *, input_type: str = "text", inputmode: str = "", required: bool = False, calc: str = "") -> str:
    req = " required" if required else ""
    mode = f" inputmode='{esc(inputmode)}'" if inputmode else ""
    calc_attr = f" data-calc='{esc(calc)}'" if calc else ""
    return f"<label>{esc(label)}<input type='{input_type}' name='{esc(name)}' value='{esc(value or '')}'{mode}{req}{calc_attr}></label>"


def select_field(label: str, name: str, options: list[tuple[str, str]], current: Any) -> str:
    opts = "".join(f"<option value='{esc(value)}' {'selected' if str(current) == value else ''}>{esc(label_text)}</option>" for value, label_text in options)
    return f"<label>{esc(label)}<select name='{esc(name)}'>{opts}</select></label>"


def last_field(label: str, name: str, default: Any, last: dict[str, str], *, input_type: str = "text", required: bool = False, calc: str = "") -> str:
    last_value = last.get(name, "")
    value = default if default not in (None, "") else last_value
    req = " required" if required else ""
    calc_attr = f" data-calc='{esc(calc)}'" if calc else ""
    hint = f"<span class='mini'>Last quarter: {esc(format_money(last_value) if last_value and input_type != 'date' else last_value)}</span>" if last_value else "<span class='mini'>No prior value</span>"
    return f"<label>{esc(label)}<input type='{input_type}' name='{esc(name)}' value='{esc(value)}' data-last='{esc(last_value)}'{req}{calc_attr}>{hint}</label>"


def people_fields(bundle: dict[str, Any], *, reveal: bool = False) -> str:
    people = {int(p["person_index"]): p for p in bundle.get("people", [])}
    rows = []
    for idx in (1, 2):
        person = people.get(idx, {})
        rows.append(
            f"""
            <div class="subpanel">
              <h3>{'Client 1' if idx == 1 else 'Client 2 / Spouse'}</h3>
              <div class="grid">
                {field('First Name', f'p{idx}_first_name', person.get('first_name'), required=idx == 1)}
                {field('Last Name', f'p{idx}_last_name', person.get('last_name'), required=idx == 1)}
                {field('DOB', f'p{idx}_dob', person.get('dob'), input_type='date')}
                {field('SSN Last 4', f'p{idx}_ssn_last4', person.get('ssn_last4') if reveal else '', inputmode='numeric')}
              </div>
            </div>
            """
        )
    return f"<section class='panel'><h2>People</h2>{''.join(rows)}</section>"


def deductible_fields(bundle: dict[str, Any]) -> str:
    items = list(bundle.get("deductible_items", []))
    rows = []
    visible = max(len(items) + 1, 2)
    for idx in range(1, min(MAX_DEDUCTIBLES, visible) + 1):
        item = items[idx - 1] if idx <= len(items) else {}
        rows.append(f"<tr><td><input name='deductible_{idx}_label' value='{esc(item.get('label', ''))}' placeholder='Home insurance'></td><td><input name='deductible_{idx}_amount' value='{esc(item.get('amount', ''))}' inputmode='decimal' placeholder='1000'></td></tr>")
    return f"<section class='panel'><h2>Insurance Deductibles</h2><table data-dynamic='deductible' data-max='{MAX_DEDUCTIBLES}'><thead><tr><th>Label</th><th>Amount</th></tr></thead><tbody>{''.join(rows)}</tbody></table><button type='button' class='secondary small' data-add-row='deductible'>Add Deductible</button></section>"


def account_fields(bundle: dict[str, Any]) -> str:
    accounts = list(bundle.get("accounts", []))
    rows = []
    visible = max(len(accounts) + 1, 3)
    for idx in range(1, min(MAX_ACCOUNTS, visible) + 1):
        account = accounts[idx - 1] if idx <= len(accounts) else {}
        rows.append(
            f"""
            <tr>
              <td>{raw_select(f'account_{idx}_category', [('', ''), ('retirement', 'Retirement'), ('non_retirement', 'Non-Retirement')], account.get('category', ''))}</td>
              <td>{raw_select(f'account_{idx}_owner_index', [('1', 'Client 1'), ('2', 'Client 2'), ('0', 'Joint')], str(account.get('owner_index', '1')))}</td>
              <td><input name="account_{idx}_account_type" value="{esc(account.get('account_type', ''))}" placeholder="IRA, Roth IRA, Brokerage"></td>
              <td><input name="account_{idx}_institution" value="{esc(account.get('institution', ''))}" placeholder="Schwab"></td>
              <td><input name="account_{idx}_account_last4" value="{esc(account.get('account_last4', ''))}" placeholder="1234"></td>
              <td><input name="account_{idx}_floor_amount" value="{esc(account.get('floor_amount', '1000'))}" inputmode="decimal"></td>
              <td><input name="account_{idx}_source_notes" value="{esc(account.get('source_notes', ''))}" placeholder="Schwab manual pull"></td>
            </tr>
            """
        )
    return f"<section class='panel'><h2>Accounts</h2><table data-dynamic='account' data-max='{MAX_ACCOUNTS}'><thead><tr><th>Category</th><th>Owner</th><th>Type</th><th>Institution</th><th>Last 4</th><th>Floor</th><th>Source Notes</th></tr></thead><tbody>{''.join(rows)}</tbody></table><button type='button' class='secondary small' data-add-row='account'>Add Account</button></section>"


def trust_fields(bundle: dict[str, Any]) -> str:
    trusts = list(bundle.get("trust_assets", []))
    rows = []
    visible = max(len(trusts) + 1, 1)
    for idx in range(1, min(MAX_TRUSTS, visible) + 1):
        trust = trusts[idx - 1] if idx <= len(trusts) else {}
        rows.append(f"<tr><td><input name='trust_{idx}_description' value='{esc(trust.get('description', ''))}' placeholder='Primary Residence'></td><td><input name='trust_{idx}_property_address' value='{esc(trust.get('property_address', ''))}' placeholder='Street, City, State'></td></tr>")
    return f"<section class='panel'><h2>Trust Assets</h2><table data-dynamic='trust' data-max='{MAX_TRUSTS}'><thead><tr><th>Description</th><th>Property Address</th></tr></thead><tbody>{''.join(rows)}</tbody></table><button type='button' class='secondary small' data-add-row='trust'>Add Trust Asset</button></section>"


def liability_fields(bundle: dict[str, Any]) -> str:
    liabilities = list(bundle.get("liabilities", []))
    rows = []
    visible = max(len(liabilities) + 1, 1)
    for idx in range(1, min(MAX_LIABILITIES, visible) + 1):
        liability = liabilities[idx - 1] if idx <= len(liabilities) else {}
        rows.append(f"<tr><td><input name='liability_{idx}_liability_type' value='{esc(liability.get('liability_type', ''))}' placeholder='Mortgage'></td><td><input name='liability_{idx}_lender' value='{esc(liability.get('lender', ''))}' placeholder='Pinnacle'></td><td><input name='liability_{idx}_interest_rate' value='{esc(liability.get('interest_rate', ''))}' inputmode='decimal' placeholder='6.25'></td><td><input name='liability_{idx}_account_last4' value='{esc(liability.get('account_last4', ''))}' placeholder='1234'></td><td><input name='liability_{idx}_source_notes' value='{esc(liability.get('source_notes', ''))}'></td></tr>")
    return f"<section class='panel'><h2>Liabilities</h2><table data-dynamic='liability' data-max='{MAX_LIABILITIES}'><thead><tr><th>Type</th><th>Lender</th><th>Interest Rate</th><th>Last 4</th><th>Source Notes</th></tr></thead><tbody>{''.join(rows)}</tbody></table><button type='button' class='secondary small' data-add-row='liability'>Add Liability</button></section>"


def raw_select(name: str, options: list[tuple[str, str]], current: Any) -> str:
    opts = "".join(f"<option value='{esc(value)}' {'selected' if str(current) == value else ''}>{esc(label)}</option>" for value, label in options)
    return f"<select name='{esc(name)}'>{opts}</select>"


def report_deductible_fields(bundle: dict[str, Any], draft: dict[str, str]) -> str:
    rows = []
    for item in bundle.get("deductible_items", []):
        key = f"deductible_{item['id']}_amount"
        rows.append(f"<tr><td>{esc(item['label'])}</td><td><input name='{key}' value='{esc(draft.get(key, item['amount']))}' data-deductible inputmode='decimal'></td></tr>")
    if not rows:
        return "<p class='notice'>No deductible line items are configured for this client.</p>"
    return f"<table><thead><tr><th>Deductible</th><th>Amount</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_account_fields(bundle: dict[str, Any], last: dict[str, str], draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for account in bundle.get("accounts", []):
        key = f"account_{account['id']}"
        label = " - ".join(part for part in [account["account_type"], account["institution"], f"*{account['account_last4']}" if account["account_last4"] else ""] if part)
        rows.append(f"<tr><td>{esc(label)}</td><td>{esc(account['category'].replace('_', ' ').title())}</td><td>{esc(owner_label(account['owner_index']))}</td><td>{last_field('Balance', f'{key}_balance', draft.get(f'{key}_balance', ''), last, required=True)}</td><td>{last_field('Cash Balance', f'{key}_cash_balance', draft.get(f'{key}_cash_balance', ''), last)}</td></tr>")
    if not rows:
        return "<p class='notice error'>No accounts are configured for this client.</p>"
    return f"<table><thead><tr><th>Account</th><th>Category</th><th>Owner</th><th>Balance</th><th>Cash</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_only_account_fields(draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for idx in range(1, MAX_REPORT_ONLY_ACCOUNTS + 1):
        rows.append(
            f"""
            <tr>
              <td>{raw_select(f'ro_account_{idx}_category', [('', ''), ('retirement', 'Retirement'), ('non_retirement', 'Non-Retirement')], draft.get(f'ro_account_{idx}_category', ''))}</td>
              <td>{raw_select(f'ro_account_{idx}_owner_index', [('1', 'Client 1'), ('2', 'Client 2'), ('0', 'Joint')], draft.get(f'ro_account_{idx}_owner_index', '1'))}</td>
              <td><input name="ro_account_{idx}_account_type" value="{esc(draft.get(f'ro_account_{idx}_account_type', ''))}" placeholder="One-time asset"></td>
              <td><input name="ro_account_{idx}_institution" value="{esc(draft.get(f'ro_account_{idx}_institution', ''))}" placeholder="Institution"></td>
              <td><input name="ro_account_{idx}_account_last4" value="{esc(draft.get(f'ro_account_{idx}_account_last4', ''))}" placeholder="1234"></td>
              <td><input name="ro_account_{idx}_balance" value="{esc(draft.get(f'ro_account_{idx}_balance', ''))}" inputmode="decimal" placeholder="Balance"></td>
              <td><input name="ro_account_{idx}_cash_balance" value="{esc(draft.get(f'ro_account_{idx}_cash_balance', ''))}" inputmode="decimal" placeholder="Cash"></td>
              <td><input name="ro_account_{idx}_source_notes" value="{esc(draft.get(f'ro_account_{idx}_source_notes', ''))}" placeholder="Manual source"></td>
              <td><label class="inline-check"><input type="checkbox" name="ro_account_{idx}_add_to_profile" value="1" {'checked' if draft.get(f'ro_account_{idx}_add_to_profile') else ''}> Add to profile</label></td>
            </tr>
            """
        )
    return f"<div class='subsection-title'>Report-only assets</div><table><thead><tr><th>Category</th><th>Owner</th><th>Type</th><th>Institution</th><th>Last 4</th><th>Balance</th><th>Cash</th><th>Source Notes</th><th>Future</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_trust_fields(bundle: dict[str, Any], last: dict[str, str], draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for trust in bundle.get("trust_assets", []):
        field_name = f"trust_{trust['id']}_value"
        rows.append(f"<tr><td>{esc(trust['description'])}</td><td>{esc(trust['property_address'])}</td><td>{last_field('Current Value', field_name, draft.get(field_name, ''), last, required=True)}</td></tr>")
    if not rows:
        return "<p class='notice'>No trust assets are configured for this client.</p>"
    return f"<table><thead><tr><th>Description</th><th>Property Address</th><th>Value</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_only_trust_fields(draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for idx in range(1, MAX_REPORT_ONLY_TRUSTS + 1):
        rows.append(
            f"""
            <tr>
              <td><input name="ro_trust_{idx}_description" value="{esc(draft.get(f'ro_trust_{idx}_description', ''))}" placeholder="Report-only property"></td>
              <td><input name="ro_trust_{idx}_property_address" value="{esc(draft.get(f'ro_trust_{idx}_property_address', ''))}" placeholder="Address"></td>
              <td><input name="ro_trust_{idx}_value" value="{esc(draft.get(f'ro_trust_{idx}_value', ''))}" inputmode="decimal" placeholder="Value"></td>
              <td><input name="ro_trust_{idx}_source_notes" value="{esc(draft.get(f'ro_trust_{idx}_source_notes', ''))}" placeholder="Zillow/manual source"></td>
              <td><label class="inline-check"><input type="checkbox" name="ro_trust_{idx}_add_to_profile" value="1" {'checked' if draft.get(f'ro_trust_{idx}_add_to_profile') else ''}> Add to profile</label></td>
            </tr>
            """
        )
    return f"<div class='subsection-title'>Report-only trust/property assets</div><table><thead><tr><th>Description</th><th>Property Address</th><th>Value</th><th>Source Notes</th><th>Future</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_liability_fields(bundle: dict[str, Any], last: dict[str, str], draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for liability in bundle.get("liabilities", []):
        field_name = f"liability_{liability['id']}_balance"
        rows.append(f"<tr><td>{esc(liability['liability_type'])}</td><td>{esc(liability.get('lender'))}</td><td>{esc(liability['interest_rate'])}%</td><td>{last_field('Balance', field_name, draft.get(field_name, ''), last, required=True)}</td></tr>")
    if not rows:
        return "<p class='notice'>No liabilities are configured for this client.</p>"
    return f"<table><thead><tr><th>Type</th><th>Lender</th><th>Rate</th><th>Balance</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_only_liability_fields(draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    rows = []
    for idx in range(1, MAX_REPORT_ONLY_LIABILITIES + 1):
        rows.append(
            f"""
            <tr>
              <td><input name="ro_liability_{idx}_liability_type" value="{esc(draft.get(f'ro_liability_{idx}_liability_type', ''))}" placeholder="Report-only liability"></td>
              <td><input name="ro_liability_{idx}_lender" value="{esc(draft.get(f'ro_liability_{idx}_lender', ''))}" placeholder="Lender"></td>
              <td><input name="ro_liability_{idx}_interest_rate" value="{esc(draft.get(f'ro_liability_{idx}_interest_rate', ''))}" inputmode="decimal" placeholder="6.25"></td>
              <td><input name="ro_liability_{idx}_account_last4" value="{esc(draft.get(f'ro_liability_{idx}_account_last4', ''))}" placeholder="1234"></td>
              <td><input name="ro_liability_{idx}_balance" value="{esc(draft.get(f'ro_liability_{idx}_balance', ''))}" inputmode="decimal" placeholder="Balance"></td>
              <td><input name="ro_liability_{idx}_source_notes" value="{esc(draft.get(f'ro_liability_{idx}_source_notes', ''))}" placeholder="Manual source"></td>
              <td><label class="inline-check"><input type="checkbox" name="ro_liability_{idx}_add_to_profile" value="1" {'checked' if draft.get(f'ro_liability_{idx}_add_to_profile') else ''}> Add to profile</label></td>
            </tr>
            """
        )
    return f"<div class='subsection-title'>Report-only liabilities</div><table><thead><tr><th>Type</th><th>Lender</th><th>Rate</th><th>Last 4</th><th>Balance</th><th>Source Notes</th><th>Future</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def empty_bundle() -> dict[str, Any]:
    return {
        "client": {"household_name": "", "household_type": "married", "monthly_salary": "", "monthly_expense_budget": "", "private_reserve_target_override": "", "source_notes": "", "archive_status": "active", "private_reserve_notes": ""},
        "people": [],
        "accounts": [],
        "liabilities": [],
        "trust_assets": [],
        "deductible_items": [],
    }


def save_client(conn: sqlite3.Connection, data: dict[str, str], client_id: int | None = None, company_id: int | None = None) -> int:
    company_id = company_id or ensure_default_company(conn)
    household_name = data.get("household_name", "").strip()
    if not household_name:
        raise ValueError("Household name is required.")
    parse_money(data.get("monthly_salary"), required=True, field="monthly salary")
    parse_money(data.get("monthly_expense_budget"), required=True, field="monthly expense budget")
    if data.get("private_reserve_target_override"):
        parse_money(data.get("private_reserve_target_override"), field="private reserve target override")
    values = (
        household_name,
        data.get("household_type", "married"),
        data.get("monthly_salary", "0").strip() or "0",
        data.get("monthly_expense_budget", "0").strip() or "0",
        data.get("private_reserve_target_override", "").strip(),
        data.get("source_notes", "").strip(),
        data.get("archive_status", "active"),
        data.get("private_reserve_notes", "").strip(),
    )
    if client_id is None:
        cur = conn.execute(
            "INSERT INTO clients (company_id, household_name, household_type, monthly_salary, monthly_expense_budget, private_reserve_target_override, source_notes, archive_status, private_reserve_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company_id, *values),
        )
        client_id = int(cur.lastrowid)
    else:
        conn.execute(
            "UPDATE clients SET company_id = ?, household_name = ?, household_type = ?, monthly_salary = ?, monthly_expense_budget = ?, private_reserve_target_override = ?, source_notes = ?, archive_status = ?, private_reserve_notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (company_id, *values, client_id),
        )
        for table in ("client_people", "accounts", "liabilities", "trust_assets", "deductible_items"):
            conn.execute(f"DELETE FROM {table} WHERE client_id = ?", (client_id,))

    for idx in (1, 2):
        first = data.get(f"p{idx}_first_name", "").strip()
        last = data.get(f"p{idx}_last_name", "").strip()
        if idx == 1 and (not first or not last):
            raise ValueError("Client 1 first and last name are required.")
        if first or last:
            conn.execute(
                "INSERT INTO client_people (client_id, person_index, first_name, last_name, dob, ssn_last4) VALUES (?, ?, ?, ?, ?, ?)",
                (client_id, idx, first, last, data.get(f"p{idx}_dob", "").strip(), data.get(f"p{idx}_ssn_last4", "").strip()[-4:]),
            )

    for idx in range(1, MAX_DEDUCTIBLES + 1):
        label = data.get(f"deductible_{idx}_label", "").strip()
        amount = data.get(f"deductible_{idx}_amount", "").strip()
        if label or amount:
            if not label:
                raise ValueError(f"Deductible row {idx} needs a label.")
            parse_money(amount, required=True, field=f"deductible {label}")
            conn.execute("INSERT INTO deductible_items (client_id, label, amount, display_order) VALUES (?, ?, ?, ?)", (client_id, label, amount, idx))

    for idx in range(1, MAX_ACCOUNTS + 1):
        account_type = data.get(f"account_{idx}_account_type", "").strip()
        category = data.get(f"account_{idx}_category", "").strip()
        if not account_type and not category:
            continue
        if not account_type or category not in {"retirement", "non_retirement"}:
            raise ValueError(f"Account row {idx} needs both a valid category and account type.")
        floor = data.get(f"account_{idx}_floor_amount", "1000").strip() or "1000"
        parse_money(floor, field=f"account row {idx} floor")
        conn.execute(
            "INSERT INTO accounts (client_id, owner_index, category, account_type, institution, account_last4, floor_amount, source_notes, display_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, int(data.get(f"account_{idx}_owner_index", "1") or 1), category, account_type, data.get(f"account_{idx}_institution", "").strip(), data.get(f"account_{idx}_account_last4", "").strip(), floor, data.get(f"account_{idx}_source_notes", "").strip(), idx),
        )

    for idx in range(1, MAX_TRUSTS + 1):
        description = data.get(f"trust_{idx}_description", "").strip()
        address = data.get(f"trust_{idx}_property_address", "").strip()
        if description or address:
            conn.execute("INSERT INTO trust_assets (client_id, description, property_address, display_order) VALUES (?, ?, ?, ?)", (client_id, description or "Trust Asset", address, idx))

    for idx in range(1, MAX_LIABILITIES + 1):
        liability_type = data.get(f"liability_{idx}_liability_type", "").strip()
        if liability_type:
            parse_rate(data.get(f"liability_{idx}_interest_rate", ""))
            conn.execute(
                "INSERT INTO liabilities (client_id, liability_type, lender, interest_rate, account_last4, source_notes, display_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_id, liability_type, data.get(f"liability_{idx}_lender", "").strip(), data.get(f"liability_{idx}_interest_rate", "").strip(), data.get(f"liability_{idx}_account_last4", "").strip(), data.get(f"liability_{idx}_source_notes", "").strip(), idx),
            )
    conn.commit()
    return client_id


def load_client_bundle(conn: sqlite3.Connection, client_id: int) -> dict[str, Any]:
    client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if client is None:
        raise ValueError("Client not found.")
    return {
        "client": dict(client),
        "people": [dict(row) for row in conn.execute("SELECT * FROM client_people WHERE client_id = ? ORDER BY person_index", (client_id,))],
        "accounts": [dict(row) for row in conn.execute("SELECT * FROM accounts WHERE client_id = ? ORDER BY display_order, id", (client_id,))],
        "liabilities": [dict(row) for row in conn.execute("SELECT * FROM liabilities WHERE client_id = ? ORDER BY display_order, id", (client_id,))],
        "trust_assets": [dict(row) for row in conn.execute("SELECT * FROM trust_assets WHERE client_id = ? ORDER BY display_order, id", (client_id,))],
        "deductible_items": [dict(row) for row in conn.execute("SELECT * FROM deductible_items WHERE client_id = ? ORDER BY display_order, id", (client_id,))],
    }


def latest_values(conn: sqlite3.Connection, client_id: int) -> dict[str, str]:
    row = conn.execute("SELECT snapshot_json FROM quarterly_reports WHERE client_id = ? ORDER BY report_date DESC, id DESC LIMIT 1", (client_id,)).fetchone()
    if row is None:
        return {}
    snapshot = json.loads(row["snapshot_json"])
    values = dict(snapshot.get("inputs", {}))
    for account in snapshot.get("accounts", []):
        values[f"account_{account['id']}_balance"] = account.get("balance", "")
        values[f"account_{account['id']}_cash_balance"] = account.get("cash_balance", "")
    for trust in snapshot.get("trust_assets", []):
        values[f"trust_{trust['id']}_value"] = trust.get("value", "")
    for liability in snapshot.get("liabilities", []):
        values[f"liability_{liability['id']}_balance"] = liability.get("balance", "")
    return values


def snapshot_from_form(bundle: dict[str, Any], data: dict[str, str]) -> dict[str, Any]:
    report_date = data.get("report_date", "").strip()
    if not report_date:
        raise ValueError("Report date is required.")
    accounts = []
    for account in bundle["accounts"]:
        label = " - ".join(part for part in [account["institution"], account["account_type"], f"*{account['account_last4']}" if account["account_last4"] else ""] if part)
        accounts.append({**account, "label": label or account["account_type"], "balance": data.get(f"account_{account['id']}_balance", ""), "cash_balance": data.get(f"account_{account['id']}_cash_balance", "0") or "0"})
    accounts.extend(report_only_accounts_from_form(data))
    trusts = [{**trust, "value": data.get(f"trust_{trust['id']}_value", "")} for trust in bundle["trust_assets"]]
    trusts.extend(report_only_trusts_from_form(data))
    liabilities = [{**liability, "balance": data.get(f"liability_{liability['id']}_balance", "")} for liability in bundle["liabilities"]]
    liabilities.extend(report_only_liabilities_from_form(data))
    deductible_total = sum(parse_money(data.get(f"deductible_{item['id']}_amount", item["amount"])) for item in bundle.get("deductible_items", []))
    return {
        "report_date": report_date,
        "client": bundle["client"],
        "people": bundle["people"],
        "accounts": accounts,
        "trust_assets": trusts,
        "liabilities": liabilities,
        "deductible_items": [{**item, "amount": data.get(f"deductible_{item['id']}_amount", item["amount"])} for item in bundle.get("deductible_items", [])],
        "inputs": {
            "report_date": report_date,
            "inflow": data.get("inflow", ""),
            "outflow": data.get("outflow", ""),
            "insurance_deductibles": str(deductible_total),
            "private_reserve_balance": data.get("private_reserve_balance", ""),
            "investment_account_balance": data.get("investment_account_balance", "0") or "0",
        },
    }


def report_only_accounts_from_form(data: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(1, MAX_REPORT_ONLY_ACCOUNTS + 1):
        category = data.get(f"ro_account_{idx}_category", "").strip()
        account_type = data.get(f"ro_account_{idx}_account_type", "").strip()
        balance = data.get(f"ro_account_{idx}_balance", "").strip()
        if not any([category, account_type, balance]):
            continue
        if category not in {"retirement", "non_retirement"} or not account_type or not balance:
            raise ValueError(f"Report-only asset row {idx} needs category, type, and balance.")
        institution = data.get(f"ro_account_{idx}_institution", "").strip()
        last4 = data.get(f"ro_account_{idx}_account_last4", "").strip()
        label = " - ".join(part for part in [institution, account_type, f"*{last4}" if last4 else ""] if part)
        rows.append(
            {
                "id": -1000 - idx,
                "owner_index": int(data.get(f"ro_account_{idx}_owner_index", "1") or 1),
                "category": category,
                "account_type": account_type,
                "institution": institution,
                "account_last4": last4,
                "floor_amount": "0",
                "source_notes": data.get(f"ro_account_{idx}_source_notes", "").strip(),
                "label": label or account_type,
                "balance": balance,
                "cash_balance": data.get(f"ro_account_{idx}_cash_balance", "0").strip() or "0",
                "source": "Report Only",
                "add_to_profile": data.get(f"ro_account_{idx}_add_to_profile") == "1",
            }
        )
    return rows


def report_only_trusts_from_form(data: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(1, MAX_REPORT_ONLY_TRUSTS + 1):
        description = data.get(f"ro_trust_{idx}_description", "").strip()
        value = data.get(f"ro_trust_{idx}_value", "").strip()
        if not any([description, value]):
            continue
        if not description or not value:
            raise ValueError(f"Report-only trust row {idx} needs description and value.")
        rows.append(
            {
                "id": -2000 - idx,
                "description": description,
                "property_address": data.get(f"ro_trust_{idx}_property_address", "").strip(),
                "source_notes": data.get(f"ro_trust_{idx}_source_notes", "").strip(),
                "value": value,
                "source": "Report Only",
                "add_to_profile": data.get(f"ro_trust_{idx}_add_to_profile") == "1",
            }
        )
    return rows


def report_only_liabilities_from_form(data: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(1, MAX_REPORT_ONLY_LIABILITIES + 1):
        liability_type = data.get(f"ro_liability_{idx}_liability_type", "").strip()
        balance = data.get(f"ro_liability_{idx}_balance", "").strip()
        if not any([liability_type, balance]):
            continue
        if not liability_type or not balance:
            raise ValueError(f"Report-only liability row {idx} needs type and balance.")
        parse_rate(data.get(f"ro_liability_{idx}_interest_rate", ""))
        rows.append(
            {
                "id": -3000 - idx,
                "liability_type": liability_type,
                "lender": data.get(f"ro_liability_{idx}_lender", "").strip(),
                "interest_rate": data.get(f"ro_liability_{idx}_interest_rate", "").strip(),
                "account_last4": data.get(f"ro_liability_{idx}_account_last4", "").strip(),
                "source_notes": data.get(f"ro_liability_{idx}_source_notes", "").strip(),
                "balance": balance,
                "source": "Report Only",
                "add_to_profile": data.get(f"ro_liability_{idx}_add_to_profile") == "1",
            }
        )
    return rows


def persist_report_only_profile_items(conn: sqlite3.Connection, client_id: int, snapshot: dict[str, Any]) -> None:
    for account in snapshot.get("accounts", []):
        if not account.get("add_to_profile"):
            continue
        display_order = next_display_order(conn, "accounts", client_id)
        conn.execute(
            "INSERT INTO accounts (client_id, owner_index, category, account_type, institution, account_last4, floor_amount, source_notes, display_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, account.get("owner_index", 1), account["category"], account["account_type"], account.get("institution", ""), account.get("account_last4", ""), account.get("floor_amount", "0"), account.get("source_notes", ""), display_order),
        )
    for trust in snapshot.get("trust_assets", []):
        if not trust.get("add_to_profile"):
            continue
        display_order = next_display_order(conn, "trust_assets", client_id)
        conn.execute(
            "INSERT INTO trust_assets (client_id, description, property_address, display_order) VALUES (?, ?, ?, ?)",
            (client_id, trust["description"], trust.get("property_address", ""), display_order),
        )
    for liability in snapshot.get("liabilities", []):
        if not liability.get("add_to_profile"):
            continue
        display_order = next_display_order(conn, "liabilities", client_id)
        conn.execute(
            "INSERT INTO liabilities (client_id, liability_type, lender, interest_rate, account_last4, source_notes, display_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, liability["liability_type"], liability.get("lender", ""), liability.get("interest_rate", ""), liability.get("account_last4", ""), liability.get("source_notes", ""), display_order),
        )


def next_display_order(conn: sqlite3.Connection, table: str, client_id: int) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX(display_order), 0) + 1 AS next_order FROM {table} WHERE client_id = ?", (client_id,)).fetchone()
    return int(row["next_order"])


def store_report(conn: sqlite3.Connection, client_id: int, snapshot: dict[str, Any], user_id: int | None = None, company_id: int | None = None) -> int:
    if company_id is None:
        client = conn.execute("SELECT company_id FROM clients WHERE id = ?", (client_id,)).fetchone()
        company_id = int(client["company_id"]) if client else ensure_default_company(conn)
    body = json.dumps(snapshot, sort_keys=True)
    cur = conn.execute("INSERT INTO quarterly_reports (company_id, client_id, report_date, snapshot_json, status, export_status) VALUES (?, ?, ?, ?, 'generated', 'not_configured')", (company_id, client_id, snapshot["report_date"], body))
    report_id = int(cur.lastrowid)
    for account in snapshot.get("accounts", []):
        conn.execute("INSERT INTO report_account_balances (report_id, source_type, source_id, balance, cash_balance) VALUES (?, ?, ?, ?, ?)", (report_id, "account", account["id"], account["balance"], account.get("cash_balance", "0")))
    for trust in snapshot.get("trust_assets", []):
        conn.execute("INSERT INTO report_account_balances (report_id, source_type, source_id, balance, cash_balance) VALUES (?, ?, ?, ?, ?)", (report_id, "trust", trust["id"], trust["value"], "0"))
    for liability in snapshot.get("liabilities", []):
        conn.execute("INSERT INTO report_account_balances (report_id, source_type, source_id, balance, cash_balance) VALUES (?, ?, ?, ?, ?)", (report_id, "liability", liability["id"], liability["balance"], "0"))
    conn.execute("INSERT INTO generated_files (report_id, file_type, filename) VALUES (?, ?, ?)", (report_id, "sacs", "sacs.pdf"))
    conn.execute("INSERT INTO generated_files (report_id, file_type, filename) VALUES (?, ?, ?)", (report_id, "tcc", "tcc.pdf"))
    conn.execute("INSERT INTO generated_files (report_id, file_type, filename) VALUES (?, ?, ?)", (report_id, "excel", "excel.xlsx"))
    conn.commit()
    return report_id


def load_report(conn: sqlite3.Connection, report_id: int) -> sqlite3.Row:
    report = conn.execute("SELECT * FROM quarterly_reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise ValueError("Report not found.")
    return report


def save_user(conn: sqlite3.Connection, data: dict[str, str], user_id: int | None = None, actor: dict[str, Any] | None = None) -> int:
    email = data.get("email", "").strip().lower()
    full_name = data.get("full_name", "").strip()
    role = data.get("role", "viewer")
    valid_roles = {"system_admin", "company_admin", "planner", "assistant", "viewer"}
    if role == "admin":
        role = "system_admin"
    if not email or not full_name or role not in valid_roles:
        raise ValueError("User name, email, and valid role are required.")
    requested_company_id = int(data.get("company_id") or 0) if data.get("company_id") else None
    if actor:
        if is_system_admin(actor.get("role")):
            company_id = requested_company_id or actor.get("company_id") or ensure_default_company(conn)
        elif is_company_admin(actor.get("role")):
            if role in {"system_admin", "company_admin"}:
                raise PermissionError("Company admins can only create Planner, Assistant, or Viewer users.")
            company_id = int(actor.get("company_id") or 0)
        else:
            raise PermissionError("Company admin access is required.")
    else:
        company_id = requested_company_id or ensure_default_company(conn)
    is_active = 1 if data.get("is_active", "1") == "1" else 0
    password = data.get("password", "")
    if user_id is None:
        if not password:
            raise ValueError("Password is required for new users.")
        cur = conn.execute("INSERT INTO users (company_id, email, full_name, role, password_hash, is_active) VALUES (?, ?, ?, ?, ?, ?)", (company_id, email, full_name, role, hash_password(password), is_active))
        conn.commit()
        return int(cur.lastrowid)
    if password:
        conn.execute("UPDATE users SET company_id = ?, email = ?, full_name = ?, role = ?, password_hash = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (company_id, email, full_name, role, hash_password(password), is_active, user_id))
    else:
        conn.execute("UPDATE users SET company_id = ?, email = ?, full_name = ?, role = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (company_id, email, full_name, role, is_active, user_id))
    conn.commit()
    return user_id


def audit(conn: sqlite3.Connection, user_id: int | None, action: str, entity_type: str, entity_id: str, metadata: dict[str, Any], company_id: int | None = None) -> None:
    conn.execute("INSERT INTO audit_events (company_id, user_id, action, entity_type, entity_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?)", (company_id, user_id, action, entity_type, entity_id, json.dumps(metadata, sort_keys=True)))


def strip_security_fields(data: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in data.items() if key != "csrf_token"}


def owner_label(owner_index: int | str) -> str:
    return {"0": "Joint", "1": "Client 1", "2": "Client 2"}.get(str(owner_index), f"Client {owner_index}")


def safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in cleaned.split("-") if part) or "client"


def client_row(row: sqlite3.Row) -> str:
    completeness = profile_completeness({"client": dict(row), "people": [], "accounts": [], "liabilities": [], "trust_assets": [], "deductible_items": []})
    status = badge("Ready" if row["last_report_date"] else "Needs first report", "ready" if row["last_report_date"] else "warn")
    return f"<tr><td><a href='/clients/{row['id']}'>{esc(row['household_name'])}</a></td><td>{esc(row['primary_contact'] or '-')}</td><td>{status}</td><td>{completeness}%</td><td>{esc(row['last_report_date'] or '-')}</td><td>{row['report_count']}</td><td>{esc(format_money(row['monthly_expense_budget']))}</td></tr>"


def profile_completeness(bundle: dict[str, Any]) -> int:
    checks = [
        bool(bundle["client"].get("household_name")),
        bool(bundle["client"].get("monthly_salary")),
        bool(bundle["client"].get("monthly_expense_budget")),
        bool(bundle.get("people")),
        bool(bundle.get("accounts")),
        bool(bundle.get("deductible_items")),
    ]
    return round(sum(1 for item in checks if item) / len(checks) * 100)


def people_summary(bundle: dict[str, Any], *, reveal: bool = False) -> str:
    parts = []
    for person in bundle.get("people", []):
        age = age_on(person.get("dob"))
        parts.append(f"{person['first_name']} {person['last_name']} (Age {age if age is not None else '-'}, {mask_ssn_last4(person.get('ssn_last4'), reveal=reveal)})")
    return " / ".join(parts) if parts else "No people configured"


def metric(label: str, value: str, kind: str = "neutral", element_id: str = "") -> str:
    id_attr = f" id='{element_id}'" if element_id else ""
    return f"<div class='metric {esc(kind)}'><span>{esc(label)}</span><strong{id_attr}>{esc(value)}</strong></div>"


def client_tables(bundle: dict[str, Any]) -> str:
    accounts = "".join(f"<tr><td>{esc(a['category'].replace('_', ' ').title())}</td><td>{esc(owner_label(a['owner_index']))}</td><td>{esc(a['account_type'])}</td><td>{esc(a['institution'])}</td><td>*{esc(a['account_last4'] or '----')}</td><td>{esc(format_money(a.get('floor_amount')))}</td></tr>" for a in bundle["accounts"]) or "<tr><td colspan='6'>No accounts configured.</td></tr>"
    liabilities = "".join(f"<tr><td>{esc(l['liability_type'])}</td><td>{esc(l.get('lender'))}</td><td>{esc(l['interest_rate'])}%</td><td>*{esc(l['account_last4'] or '----')}</td></tr>" for l in bundle["liabilities"]) or "<tr><td colspan='4'>No liabilities configured.</td></tr>"
    deductibles = "".join(f"<tr><td>{esc(d['label'])}</td><td>{esc(format_money(d['amount']))}</td></tr>" for d in bundle["deductible_items"]) or "<tr><td colspan='2'>No deductibles configured.</td></tr>"
    return f"<section class='panel'><h2>Profile Assets</h2><table><thead><tr><th>Category</th><th>Owner</th><th>Type</th><th>Institution</th><th>Last 4</th><th>Floor</th></tr></thead><tbody>{accounts}</tbody></table></section><section class='panel'><h2>Deductibles</h2><table><thead><tr><th>Label</th><th>Amount</th></tr></thead><tbody>{deductibles}</tbody></table></section><section class='panel'><h2>Profile Liabilities</h2><table><thead><tr><th>Type</th><th>Lender</th><th>Rate</th><th>Last 4</th></tr></thead><tbody>{liabilities}</tbody></table></section>"


def draft_table(rows: list[sqlite3.Row]) -> str:
    body = "".join(f"<tr><td>{esc(r['report_date'] or '-')}</td><td>{badge(r['status'], 'warn')}</td><td>{esc(r['updated_at'])}</td></tr>" for r in rows) or "<tr><td colspan='3'>No drafts saved.</td></tr>"
    return f"<table><thead><tr><th>Report Date</th><th>Status</th><th>Updated</th></tr></thead><tbody>{body}</tbody></table>"


def report_table(rows: list[sqlite3.Row], *, include_client: bool = False) -> str:
    client_head = "<th>Client</th>" if include_client else ""
    body = ""
    for r in rows:
        client_cell = f"<td>{esc(r['household_name'])}</td>" if include_client else ""
        body += f"<tr>{client_cell}<td><a href='/reports/{r['id']}'>{esc(r['report_date'])}</a></td><td>{badge(r['status'], 'ready')}</td><td>{badge(r['export_status'], 'neutral')}</td><td><a href='/reports/{r['id']}/sacs.pdf'>SACS</a> / <a href='/reports/{r['id']}/tcc.pdf'>TCC</a> / <a href='/reports/{r['id']}/excel.xlsx'>Excel</a></td></tr>"
    body = body or f"<tr><td colspan='{5 if include_client else 4}'>No reports generated.</td></tr>"
    return f"<table><thead><tr>{client_head}<th>Report Date</th><th>Status</th><th>Export</th><th>Downloads</th></tr></thead><tbody>{body}</tbody></table>"


def audit_table(rows: list[sqlite3.Row], *, include_user: bool = False) -> str:
    user_head = "<th>User</th>" if include_user else ""
    body = ""
    for r in rows:
        user_cell = f"<td>{esc(r['full_name'] or 'System')}</td>" if include_user else ""
        body += f"<tr>{user_cell}<td>{esc(r['action'])}</td><td>{esc(r['entity_type'])}</td><td>{esc(r['entity_id'])}</td><td>{esc(r['created_at'])}</td></tr>"
    body = body or f"<tr><td colspan='{5 if include_user else 4}'>No activity yet.</td></tr>"
    return f"<table><thead><tr>{user_head}<th>Action</th><th>Entity</th><th>ID</th><th>Time</th></tr></thead><tbody>{body}</tbody></table>"


def snapshot_tables(snapshot: dict[str, Any]) -> str:
    accounts = "".join(f"<tr><td>{esc(a.get('source', 'Profile'))}</td><td>{esc(a['category'].replace('_', ' ').title())}</td><td>{esc(owner_label(a['owner_index']))}</td><td>{esc(a['label'])}</td><td>{esc(format_money(a['balance']))}</td><td>{esc(format_money(a.get('cash_balance')))}</td></tr>" for a in snapshot.get("accounts", [])) or "<tr><td colspan='6'>No accounts.</td></tr>"
    trusts = "".join(f"<tr><td>{esc(t.get('source', 'Profile'))}</td><td>{esc(t.get('description'))}</td><td>{esc(t.get('property_address'))}</td><td>{esc(format_money(t.get('value')))}</td></tr>" for t in snapshot.get("trust_assets", [])) or "<tr><td colspan='4'>No trust assets.</td></tr>"
    liabilities = "".join(f"<tr><td>{esc(l.get('source', 'Profile'))}</td><td>{esc(l['liability_type'])}</td><td>{esc(l.get('lender'))}</td><td>{esc(l.get('interest_rate'))}%</td><td>{esc(format_money(l['balance']))}</td></tr>" for l in snapshot.get("liabilities", [])) or "<tr><td colspan='5'>No liabilities.</td></tr>"
    return f"<section class='panel'><h2>Account Snapshot</h2><table><thead><tr><th>Source</th><th>Category</th><th>Owner</th><th>Account</th><th>Balance</th><th>Cash</th></tr></thead><tbody>{accounts}</tbody></table></section><section class='panel'><h2>Trust Assets</h2><table><thead><tr><th>Source</th><th>Description</th><th>Address</th><th>Value</th></tr></thead><tbody>{trusts}</tbody></table></section><section class='panel'><h2>Liabilities</h2><table><thead><tr><th>Source</th><th>Type</th><th>Lender</th><th>Rate</th><th>Balance</th></tr></thead><tbody>{liabilities}</tbody></table></section>"

from __future__ import annotations

from io import BytesIO
from textwrap import shorten
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .calculations import age_on, format_money


BLUE = colors.HexColor("#1f5f9f")
GREEN = colors.HexColor("#d9ead3")
GREEN_STROKE = colors.HexColor("#5d8a4b")
RED = colors.HexColor("#f4cccc")
RED_STROKE = colors.HexColor("#b54d4d")
LIGHT_BLUE = colors.HexColor("#d9eaf7")
GRAY = colors.HexColor("#eeeeee")
INK = colors.HexColor("#1d2733")


def _header(c: canvas.Canvas, title: str, client_name: str, report_date: str, width: float, height: float) -> None:
    c.setFillColor(BLUE)
    c.rect(0, height - 0.62 * inch, width, 0.62 * inch, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.55 * inch, height - 0.38 * inch, title)
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 0.55 * inch, height - 0.24 * inch, client_name)
    c.drawRightString(width - 0.55 * inch, height - 0.43 * inch, report_date)
    c.setFillColor(INK)


def _centered(c: canvas.Canvas, x: float, y: float, text: str, font: str = "Helvetica", size: int = 10) -> None:
    c.setFont(font, size)
    c.drawCentredString(x, y, text)


def _bubble(c: canvas.Canvas, x: float, y: float, w: float, h: float, fill, stroke, lines: list[str], size: int = 10) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, 18, stroke=1, fill=1)
    c.setFillColor(INK)
    top = y + h - 0.23 * inch
    for index, line in enumerate(lines[:5]):
        c.setFont("Helvetica-Bold" if index == 0 else "Helvetica", size if index == 0 else max(size - 1, 8))
        c.drawCentredString(x + w / 2, top - index * 0.19 * inch, shorten(str(line), width=34, placeholder="..."))


def _arrow(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float, color) -> None:
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(3)
    c.line(x1, y1, x2, y2)
    direction = 1 if x2 >= x1 else -1
    c.line(x2, y2, x2 - direction * 10, y2 + 6)
    c.line(x2, y2, x2 - direction * 10, y2 - 6)
    c.setLineWidth(1)
    c.setStrokeColor(INK)
    c.setFillColor(INK)


def build_sacs_pdf(snapshot: dict[str, Any]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER
    client_name = snapshot["client"]["household_name"]
    report_date = snapshot["report_date"]
    totals = snapshot["totals"]

    _header(c, "Simple Automated Cash Flow System (SACS)", client_name, report_date, width, height)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, height - 1.15 * inch, "Monthly Cash Flow")

    cy = height - 3.05 * inch
    _bubble(c, 0.55 * inch, cy, 1.9 * inch, 1.25 * inch, GREEN, GREEN_STROKE, ["Inflow", format_money(totals["inflow"]), "after-tax income"], 11)
    _bubble(c, 3.05 * inch, cy, 1.9 * inch, 1.25 * inch, RED, RED_STROKE, ["Outflow", format_money(totals["outflow"]), "expense budget"], 11)
    _bubble(c, 5.55 * inch, cy, 1.9 * inch, 1.25 * inch, LIGHT_BLUE, BLUE, ["Private Reserve", format_money(totals["excess"]), "monthly excess"], 11)
    _arrow(c, 2.48 * inch, cy + 0.62 * inch, 3.02 * inch, cy + 0.62 * inch, GREEN_STROKE)
    _arrow(c, 4.98 * inch, cy + 0.62 * inch, 5.52 * inch, cy + 0.62 * inch, BLUE)

    c.setFont("Helvetica", 10)
    c.drawCentredString(width / 2, height - 4.62 * inch, "Excess cash is directed to the Private Reserve after the agreed monthly outflow is funded.")

    c.setFillColor(GRAY)
    c.roundRect(0.8 * inch, 1.0 * inch, width - 1.6 * inch, 1.2 * inch, 10, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1.05 * inch, 1.82 * inch, "Quarterly Snapshot")
    c.setFont("Helvetica", 10)
    c.drawString(1.05 * inch, 1.52 * inch, f"Private Reserve Balance: {format_money(totals['private_reserve_balance'])}")
    c.drawString(1.05 * inch, 1.25 * inch, f"Investment Account Balance: {format_money(totals['investment_account_balance'])}")
    c.drawRightString(width - 1.05 * inch, 1.52 * inch, f"Private Reserve Target: {format_money(totals['private_reserve_target'])}")
    c.drawRightString(width - 1.05 * inch, 1.25 * inch, f"Insurance Deductibles: {format_money(totals['insurance_deductibles'])}")

    c.showPage()
    _header(c, "SACS Reserve Detail", client_name, report_date, width, height)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.7 * inch, height - 1.25 * inch, "Private Reserve")
    _bubble(c, 0.8 * inch, height - 3.0 * inch, 2.2 * inch, 1.15 * inch, LIGHT_BLUE, BLUE, ["Current Balance", format_money(totals["private_reserve_balance"])], 12)
    _bubble(c, 3.15 * inch, height - 3.0 * inch, 2.2 * inch, 1.15 * inch, GRAY, colors.darkgray, ["Target", format_money(totals["private_reserve_target"])], 12)
    _bubble(c, 5.5 * inch, height - 3.0 * inch, 2.0 * inch, 1.15 * inch, GREEN, GREEN_STROKE, ["Monthly Excess", format_money(totals["excess"])], 12)
    c.setFont("Helvetica", 10)
    c.drawString(0.8 * inch, height - 3.55 * inch, "Target formula: six months of expenses plus all insurance deductibles.")
    c.drawString(0.8 * inch, height - 3.85 * inch, "Liabilities are intentionally excluded from this SACS reserve calculation.")
    c.save()
    return buf.getvalue()


def build_tcc_pdf(snapshot: dict[str, Any]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(LETTER))
    width, height = landscape(LETTER)
    client_name = snapshot["client"]["household_name"]
    report_date = snapshot["report_date"]
    totals = snapshot["totals"]

    _header(c, "Total Client Chart (TCC)", client_name, report_date, width, height)
    _draw_people(c, snapshot, width, height)
    _draw_accounts(c, snapshot, width, height)
    _draw_trusts(c, snapshot, width, height)
    _draw_liabilities(c, snapshot, width, height)
    _draw_totals(c, totals, width)
    c.save()
    return buf.getvalue()


def _draw_people(c: canvas.Canvas, snapshot: dict[str, Any], width: float, height: float) -> None:
    people = snapshot.get("people", [])
    for index, person in enumerate(people[:2]):
        x = 0.45 * inch + index * 2.15 * inch
        name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        age = age_on(person.get("dob"), _date_from_snapshot(snapshot))
        lines = [name or f"Client {index + 1}", f"Age: {age if age is not None else '-'}", f"DOB: {person.get('dob') or '-'}", f"SSN: ***-{person.get('ssn_last4') or '----'}"]
        _bubble(c, x, height - 1.72 * inch, 1.9 * inch, 0.9 * inch, GREEN, GREEN_STROKE, lines, 8)


def _date_from_snapshot(snapshot: dict[str, Any]):
    from datetime import datetime

    try:
        return datetime.strptime(snapshot["report_date"], "%Y-%m-%d").date()
    except Exception:
        return None


def _draw_accounts(c: canvas.Canvas, snapshot: dict[str, Any], width: float, height: float) -> None:
    retirement = [a for a in snapshot.get("accounts", []) if a.get("category") == "retirement"]
    non_retirement = [a for a in snapshot.get("accounts", []) if a.get("category") == "non_retirement"]
    _account_grid(c, retirement, 4.95 * inch, height - 1.62 * inch, 2.05 * inch, 0.68 * inch, "Retirement")
    _account_grid(c, non_retirement, 0.45 * inch, 1.55 * inch, 2.05 * inch, 0.68 * inch, "Non-Retirement")


def _account_grid(c: canvas.Canvas, accounts: list[dict[str, Any]], x: float, y: float, w: float, h: float, title: str) -> None:
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(INK)
    c.drawString(x, y + 0.83 * inch, title)
    for index, account in enumerate(accounts[:12]):
        col = index % 3
        row = index // 3
        bx = x + col * (w + 0.12 * inch)
        by = y - row * (h + 0.12 * inch)
        lines = [
            account.get("label") or account.get("account_type", "Account"),
            f"Acct: *{account.get('account_last4') or '----'}",
            f"Balance: {format_money(account.get('balance'))}",
            f"Cash: {format_money(account.get('cash_balance'))}",
        ]
        _bubble(c, bx, by, w, h, colors.white, BLUE, lines, 7)


def _draw_trusts(c: canvas.Canvas, snapshot: dict[str, Any], width: float, height: float) -> None:
    trusts = snapshot.get("trust_assets", [])
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(width / 2, height / 2 + 0.8 * inch, "Trust")
    if not trusts:
        _bubble(c, width / 2 - 1.2 * inch, height / 2 - 0.05 * inch, 2.4 * inch, 0.85 * inch, GRAY, colors.darkgray, ["No Trust Assets"], 9)
        return
    trust = trusts[0]
    lines = [
        trust.get("description") or "Trust Asset",
        shorten(trust.get("property_address", ""), width=30, placeholder="..."),
        format_money(trust.get("value")),
    ]
    _bubble(c, width / 2 - 1.35 * inch, height / 2 - 0.15 * inch, 2.7 * inch, 1.0 * inch, LIGHT_BLUE, BLUE, lines, 9)


def _draw_liabilities(c: canvas.Canvas, snapshot: dict[str, Any], width: float, height: float) -> None:
    c.setFont("Helvetica-Bold", 11)
    c.drawString(width - 2.75 * inch, 2.62 * inch, "Liabilities")
    for index, liability in enumerate(snapshot.get("liabilities", [])[:3]):
        y = 2.0 * inch - index * 0.68 * inch
        lines = [
            liability.get("liability_type", "Liability"),
            f"Rate: {liability.get('interest_rate') or '-'}%",
            format_money(liability.get("balance")),
        ]
        _bubble(c, width - 2.75 * inch, y, 2.25 * inch, 0.56 * inch, RED, RED_STROKE, lines, 7)


def _draw_totals(c: canvas.Canvas, totals: dict[str, Any], width: float) -> None:
    items = [
        ("Client 1 Retirement", totals["client_1_retirement"]),
        ("Client 2 Retirement", totals["client_2_retirement"]),
        ("Non-Retirement", totals["non_retirement"]),
        ("Grand Total", totals["grand_total"]),
        ("Liabilities (Separate)", totals["liabilities"]),
    ]
    x = 0.45 * inch
    y = 0.55 * inch
    box_w = (width - 0.9 * inch) / len(items) - 0.08 * inch
    for label, value in items:
        _bubble(c, x, y, box_w, 0.58 * inch, GRAY, colors.darkgray, [label, format_money(value)], 7)
        x += box_w + 0.1 * inch

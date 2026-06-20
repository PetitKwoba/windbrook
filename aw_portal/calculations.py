from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


Money = Decimal


def parse_money(value: Any, *, required: bool = False, field: str = "value") -> Money:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return Decimal("0")
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        if required:
            raise ValueError(f"{field} is required")
        return Decimal("0")
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a valid currency amount") from exc


def parse_rate(value: Any) -> Decimal:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return Decimal("0")
    try:
        return Decimal(text).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("interest rate must be a valid number") from exc


def format_money(value: Any) -> str:
    amount = Decimal(str(value or "0")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"${amount:,.0f}"


def format_money_precise(value: Any) -> str:
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${amount:,.2f}"


def age_on(dob: str | None, on_date: date | None = None) -> int | None:
    if not dob:
        return None
    try:
        born = datetime.strptime(dob, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = on_date or date.today()
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return years


def calculate_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    inputs = snapshot["inputs"]
    accounts = snapshot.get("accounts", [])
    trust_assets = snapshot.get("trust_assets", [])
    liabilities = snapshot.get("liabilities", [])

    inflow = parse_money(inputs.get("inflow"), required=True, field="inflow")
    outflow = parse_money(inputs.get("outflow"), required=True, field="outflow")
    insurance_deductibles = parse_money(inputs.get("insurance_deductibles"))
    private_reserve_balance = parse_money(inputs.get("private_reserve_balance"))
    investment_account_balance = parse_money(inputs.get("investment_account_balance"))

    retirement_by_owner = {1: Decimal("0"), 2: Decimal("0")}
    non_retirement_total = Decimal("0")
    for account in accounts:
        balance = parse_money(account.get("balance"), required=True, field=account.get("label", "account balance"))
        cash_balance = parse_money(account.get("cash_balance"))
        account["balance"] = str(balance)
        account["cash_balance"] = str(cash_balance)
        if account.get("category") == "retirement":
            owner = int(account.get("owner_index") or 1)
            retirement_by_owner[owner] = retirement_by_owner.get(owner, Decimal("0")) + balance
        elif account.get("category") == "non_retirement":
            non_retirement_total += balance

    trust_total = Decimal("0")
    for trust in trust_assets:
        value = parse_money(trust.get("value"), required=True, field=trust.get("description", "trust value"))
        trust["value"] = str(value)
        trust_total += value

    liabilities_total = Decimal("0")
    for liability in liabilities:
        balance = parse_money(liability.get("balance"), required=True, field=liability.get("liability_type", "liability"))
        liability["balance"] = str(balance)
        liabilities_total += balance

    client_1_retirement = retirement_by_owner.get(1, Decimal("0"))
    client_2_retirement = retirement_by_owner.get(2, Decimal("0"))
    grand_total = client_1_retirement + client_2_retirement + non_retirement_total + trust_total
    override = snapshot.get("client", {}).get("private_reserve_target_override")
    private_reserve_target = parse_money(override) if override else (outflow * Decimal("6")) + insurance_deductibles

    totals = {
        "inflow": str(inflow),
        "outflow": str(outflow),
        "excess": str(inflow - outflow),
        "insurance_deductibles": str(insurance_deductibles),
        "private_reserve_balance": str(private_reserve_balance),
        "investment_account_balance": str(investment_account_balance),
        "private_reserve_target": str(private_reserve_target),
        "client_1_retirement": str(client_1_retirement),
        "client_2_retirement": str(client_2_retirement),
        "non_retirement": str(non_retirement_total),
        "trust": str(trust_total),
        "grand_total": str(grand_total),
        "liabilities": str(liabilities_total),
    }
    snapshot["totals"] = totals
    return snapshot

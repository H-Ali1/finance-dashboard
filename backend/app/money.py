"""Money is stored as integer minor units (pence) everywhere.

Floats are never used for balances or transaction amounts; conversion to a
float happens only at the API boundary, for display.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_minor(amount: str | float | int | Decimal | None) -> int:
    """Convert a major-unit amount ("12.34", 12.34) to pence."""
    if amount is None:
        return 0
    try:
        dec = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return 0
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_major(minor: int | None) -> float:
    """Convert pence to a float for JSON output."""
    return round((minor or 0) / 100, 2)


def fmt(minor: int | None, currency: str = "GBP") -> str:
    symbol = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, "")
    value = (minor or 0) / 100
    return f"-{symbol}{abs(value):,.2f}" if value < 0 else f"{symbol}{value:,.2f}"

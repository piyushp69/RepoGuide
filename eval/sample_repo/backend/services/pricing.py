"""Price calculations: discount codes, sales tax, shipping and order totals."""
from models import OrderItem

# Percentage discounts keyed by code. Codes are matched case-insensitively.
DISCOUNT_CODES = {
    "WELCOME10": 10,
    "SPRING25": 25,
    "STAFF50": 50,
}
FREE_SHIPPING_THRESHOLD_CENTS = 5000
SHIPPING_FEE_CENTS = 499


class InvalidDiscountCode(ValueError):
    pass


def apply_discount(amount_cents: int, code: str | None) -> int:
    """Reduce an amount by the percentage attached to a discount code."""
    if not code:
        return amount_cents
    percent = DISCOUNT_CODES.get(code.strip().upper())
    if percent is None:
        raise InvalidDiscountCode(f"Unknown discount code: {code}")
    return amount_cents - (amount_cents * percent) // 100


def calculate_tax(amount_cents: int, tax_rate: float) -> int:
    """Sales tax on an amount, rounded half-up to the nearest cent."""
    return int(amount_cents * tax_rate + 0.5)


def shipping_cost(subtotal_cents: int) -> int:
    """Orders at or above the free-shipping threshold ship for free."""
    return 0 if subtotal_cents >= FREE_SHIPPING_THRESHOLD_CENTS else SHIPPING_FEE_CENTS


def compute_order_total(items: list[OrderItem], discount_code: str | None, tax_rate: float) -> int:
    """Subtotal, then discount, then tax on the discounted amount, then shipping (integer cents)."""
    subtotal = sum(item.line_total_cents for item in items)
    discounted = apply_discount(subtotal, discount_code)
    tax = calculate_tax(discounted, tax_rate)
    return discounted + tax + shipping_cost(subtotal)

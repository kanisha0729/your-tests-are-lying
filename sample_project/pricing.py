"""Sample module: order pricing for a small online store.

This is the code under test. Its test suite looks fine at a glance
but misses several realistic bugs. That gap is what the agent exposes.
"""


def bulk_discount(quantity: int) -> float:
    """Discount rate by quantity: 10+ items get 10%, 50+ items get 20%."""
    if quantity >= 50:
        return 0.20
    if quantity >= 10:
        return 0.10
    return 0.0


def shipping_fee(subtotal: float, is_member: bool) -> float:
    """Members always ship free. Others ship free at 500 or more, else 40."""
    if is_member or subtotal >= 500:
        return 0.0
    return 40.0


def apply_coupon(subtotal: float, coupon: str) -> float:
    """SAVE50 takes 50 off orders of 300 or more. Never goes below zero."""
    if coupon == "SAVE50" and subtotal >= 300:
        return max(subtotal - 50, 0.0)
    return subtotal


def order_total(unit_price: float, quantity: int, is_member: bool = False,
                coupon: str = "") -> float:
    """Full order total, rounded to 2 decimal places."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    subtotal = unit_price * quantity
    subtotal = subtotal * (1 - bulk_discount(quantity))
    subtotal = apply_coupon(subtotal, coupon)
    total = subtotal + shipping_fee(subtotal, is_member)
    return round(total, 2)

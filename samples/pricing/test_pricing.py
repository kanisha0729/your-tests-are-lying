"""Weak on purpose: every function is called and every test passes,
but no test sits on a boundary."""
import pytest

from pricing import apply_coupon, bulk_discount, order_total, shipping_fee


def test_bulk_discount_small_order():
    assert bulk_discount(1) == 0.0


def test_bulk_discount_large_order():
    assert bulk_discount(100) == 0.20


def test_shipping_non_member_small_order():
    assert shipping_fee(100, False) == 40.0


def test_coupon_applies():
    assert apply_coupon(1000, "SAVE50") == 950


def test_order_total_basic():
    assert order_total(100, 2) == 240.0


def test_order_total_rejects_zero_quantity():
    with pytest.raises(ValueError):
        order_total(100, 0)

"""Cent accounting and board-specific quantity constraints."""

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from .models import finite, integer, symbol


def cents(value):
    finite(value, "money")
    return int(
        (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def price_cents(value):
    result = cents(value)
    if result <= 0:
        raise ValueError("Price must be at least one cent")
    return result


@dataclass(frozen=True)
class BoardRule:
    minimum: int
    step: int

    def floor_buy(self, quantity):
        quantity = max(0, int(quantity))
        if quantity < self.minimum:
            return 0
        return self.minimum + ((quantity - self.minimum) // self.step) * self.step

    def valid_sell(self, quantity, total):
        integer(quantity, "quantity", minimum=1)
        return quantity <= total and (
            quantity == total
            or (quantity >= self.minimum and (quantity - self.minimum) % self.step == 0)
        )


def board_rule(code):
    code = symbol(code)
    if code.startswith(("688", "689")):
        return BoardRule(200, 1)
    if code.startswith(("4", "8", "92")):
        return BoardRule(100, 1)
    return BoardRule(100, 100)

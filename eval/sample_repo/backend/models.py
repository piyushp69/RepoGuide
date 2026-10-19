"""Domain models shared by the services and routes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


@dataclass
class Product:
    id: int
    name: str
    price_cents: int
    stock: int

    def is_available(self, quantity: int) -> bool:
        return self.stock >= quantity


@dataclass
class OrderItem:
    product_id: int
    quantity: int
    unit_price_cents: int

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass
class Order:
    customer_email: str
    items: list[OrderItem]
    status: OrderStatus = OrderStatus.PENDING
    discount_code: str | None = None
    payment_reference: str | None = None
    total_cents: int = 0
    id: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def subtotal_cents(self) -> int:
        return sum(item.line_total_cents for item in self.items)

    def can_cancel(self) -> bool:
        """Only orders that have not shipped yet can be cancelled."""
        return self.status in (OrderStatus.PENDING, OrderStatus.PAID)

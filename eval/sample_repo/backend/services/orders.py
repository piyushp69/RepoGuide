"""Order lifecycle: placing, paying, cancelling and shipping orders."""
import sqlite3

from config import Config
from db import transaction
from models import Order, OrderItem, OrderStatus
from services.inventory import check_low_stock, release_stock, reserve_stock
from services.notifications import send_order_confirmation, send_shipping_notice
from services.pricing import compute_order_total


class OrderNotFound(LookupError):
    pass


class InvalidOrderState(RuntimeError):
    pass


class OrderService:
    """Coordinates pricing, inventory and notifications for orders."""

    def __init__(self, conn: sqlite3.Connection, config: Config):
        self.conn = conn
        self.config = config

    def create_order(self, customer_email: str, items: list[OrderItem], discount_code: str | None = None) -> Order:
        """Price the order, reserve stock, persist it and e-mail a confirmation."""
        if not items:
            raise ValueError("An order needs at least one item")
        order = Order(customer_email=customer_email, items=items, discount_code=discount_code)
        order.total_cents = compute_order_total(items, discount_code, self.config.tax_rate)
        with transaction(self.conn):
            for item in items:
                reserve_stock(self.conn, item.product_id, item.quantity)
            cursor = self.conn.execute(
                "INSERT INTO orders (customer_email, status, discount_code, total_cents, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (customer_email, order.status.value, discount_code, order.total_cents, order.created_at),
            )
            order.id = cursor.lastrowid
            self.conn.executemany(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
                [(order.id, i.product_id, i.quantity, i.unit_price_cents) for i in items],
            )
        check_low_stock(self.conn, self.config.low_stock_threshold)
        send_order_confirmation(order, self.config.smtp_host, self.config.sender_email)
        return order

    def get_order(self, order_id: int) -> Order:
        row = self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise OrderNotFound(order_id)
        items = [
            OrderItem(r["product_id"], r["quantity"], r["unit_price_cents"])
            for r in self.conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,))
        ]
        return Order(
            customer_email=row["customer_email"], items=items, status=OrderStatus(row["status"]),
            discount_code=row["discount_code"], payment_reference=row["payment_reference"],
            total_cents=row["total_cents"], id=row["id"], created_at=row["created_at"],
        )

    def mark_paid(self, order_id: int, payment_reference: str) -> Order:
        """Record the payment reference supplied by the checkout provider and mark the order paid."""
        order = self.get_order(order_id)
        if order.status != OrderStatus.PENDING:
            raise InvalidOrderState(f"Order {order_id} is already {order.status.value}")
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE orders SET status = ?, payment_reference = ? WHERE id = ?",
                (OrderStatus.PAID.value, payment_reference, order_id),
            )
        order.status, order.payment_reference = OrderStatus.PAID, payment_reference
        return order

    def cancel_order(self, order_id: int) -> Order:
        """Cancel an order that has not shipped and put its items back in stock."""
        order = self.get_order(order_id)
        if not order.can_cancel():
            raise InvalidOrderState(f"Order {order_id} is {order.status.value} and cannot be cancelled")
        with transaction(self.conn):
            for item in order.items:
                release_stock(self.conn, item.product_id, item.quantity)
            self.conn.execute("UPDATE orders SET status = ? WHERE id = ?", (OrderStatus.CANCELLED.value, order_id))
        order.status = OrderStatus.CANCELLED
        return order

    def mark_shipped(self, order_id: int) -> Order:
        """Move a paid order to shipped and notify the customer by e-mail."""
        order = self.get_order(order_id)
        if order.status != OrderStatus.PAID:
            raise InvalidOrderState(f"Only paid orders can ship; order {order_id} is {order.status.value}")
        with transaction(self.conn):
            self.conn.execute("UPDATE orders SET status = ? WHERE id = ?", (OrderStatus.SHIPPED.value, order_id))
        order.status = OrderStatus.SHIPPED
        send_shipping_notice(order, self.config.smtp_host, self.config.sender_email)
        return order

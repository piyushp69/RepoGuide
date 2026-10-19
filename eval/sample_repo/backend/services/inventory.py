"""Stock management for products."""
import logging
import sqlite3

log = logging.getLogger(__name__)


class OutOfStockError(Exception):
    def __init__(self, product_id: int, requested: int, available: int):
        super().__init__(f"Product {product_id}: requested {requested}, only {available} left")
        self.product_id = product_id


def get_stock(conn: sqlite3.Connection, product_id: int) -> int:
    row = conn.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
    return row["stock"] if row else 0


def reserve_stock(conn: sqlite3.Connection, product_id: int, quantity: int) -> None:
    """Decrement stock for an order line, refusing to go below zero."""
    available = get_stock(conn, product_id)
    if available < quantity:
        raise OutOfStockError(product_id, quantity, available)
    conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))


def release_stock(conn: sqlite3.Connection, product_id: int, quantity: int) -> None:
    """Return previously reserved units to stock (used when an order is cancelled)."""
    conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (quantity, product_id))


def check_low_stock(conn: sqlite3.Connection, threshold: int) -> list[int]:
    """Log a warning for every product whose stock fell below the threshold."""
    rows = conn.execute("SELECT id, name, stock FROM products WHERE stock < ?", (threshold,)).fetchall()
    for row in rows:
        log.warning("Low stock for %s (id=%s): %s left", row["name"], row["id"], row["stock"])
    return [row["id"] for row in rows]

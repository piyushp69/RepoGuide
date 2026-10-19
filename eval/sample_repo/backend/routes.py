"""HTTP endpoints for products and orders."""
from flask import Blueprint, current_app, jsonify, request

from auth import require_auth
from models import OrderItem
from services.inventory import OutOfStockError
from services.orders import InvalidOrderState, OrderNotFound, OrderService
from services.pricing import InvalidDiscountCode

api = Blueprint("api", __name__, url_prefix="/api")


def _service() -> OrderService:
    return current_app.extensions["order_service"]


@api.get("/products")
def list_products():
    """Return every product with its price and remaining stock, sorted by name."""
    rows = _service().conn.execute("SELECT id, name, price_cents, stock FROM products ORDER BY name").fetchall()
    return jsonify([dict(row) for row in rows])


@api.post("/orders")
def create_order_route():
    """Place an order from a JSON body: {email, items: [{product_id, quantity}], discount_code}."""
    payload = request.get_json(force=True)
    if not payload or "email" not in payload:
        return jsonify({"error": "email is required"}), 400
    conn = _service().conn
    items = []
    for line in payload.get("items", []):
        price = conn.execute("SELECT price_cents FROM products WHERE id = ?", (line["product_id"],)).fetchone()
        if price is None:
            return jsonify({"error": f"unknown product {line['product_id']}"}), 404
        items.append(OrderItem(line["product_id"], int(line["quantity"]), price["price_cents"]))
    try:
        order = _service().create_order(payload["email"], items, payload.get("discount_code"))
    except (OutOfStockError, InvalidDiscountCode, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": order.id, "status": order.status.value, "total_cents": order.total_cents}), 201


@api.post("/orders/<int:order_id>/cancel")
def cancel_order_route(order_id: int):
    try:
        order = _service().cancel_order(order_id)
    except OrderNotFound:
        return jsonify({"error": "order not found"}), 404
    except InvalidOrderState as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"id": order.id, "status": order.status.value})


def register_staff_routes(app, secret_key: str) -> None:
    """Staff-only endpoints; every one of them requires a bearer token."""

    @app.post("/api/orders/<int:order_id>/ship")
    @require_auth(secret_key)
    def ship_order_route(order_id: int):
        try:
            order = _service().mark_shipped(order_id)
        except OrderNotFound:
            return jsonify({"error": "order not found"}), 404
        except InvalidOrderState as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"id": order.id, "status": order.status.value})

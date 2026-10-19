"""ShopLite application factory and development entry point."""
import logging

from flask import Flask

from config import load_config
from db import get_connection, init_db
from routes import api, register_staff_routes
from services.orders import OrderService


def create_app() -> Flask:
    """Build the Flask app: load config, prepare the database and register the routes."""
    config = load_config()
    init_db(config.database_path)
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    conn = get_connection(config.database_path)
    app.extensions["order_service"] = OrderService(conn, config)
    app.register_blueprint(api)
    register_staff_routes(app, config.secret_key)
    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=True)

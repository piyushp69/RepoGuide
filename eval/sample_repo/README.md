# ShopLite

ShopLite is a small online shop: a Flask + SQLite REST API for products and orders,
and a React/TypeScript checkout form.

## Features

- Browse products and their remaining stock
- Place orders with discount codes, sales tax and a shipping fee
- Stock is reserved when an order is placed and released when it is cancelled
- Order confirmation and shipping e-mails
- Token-based authentication for staff-only endpoints

## Project layout

- `backend/app.py` – application factory and development entry point
- `backend/routes.py` – HTTP endpoints for products and orders
- `backend/services/` – business logic (orders, pricing, inventory, notifications)
- `backend/db.py` – SQLite schema and connection helpers
- `backend/auth.py` – password hashing and signed API tokens
- `frontend/src/` – React checkout UI that talks to the API

## Running locally

```bash
pip install -r requirements.txt
cd backend
python app.py
```

The API listens on http://127.0.0.1:5000. Configuration is read from `SHOPLITE_*`
environment variables (see `backend/config.py`).

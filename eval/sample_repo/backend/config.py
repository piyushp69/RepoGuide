"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_path: str
    secret_key: str
    tax_rate: float
    low_stock_threshold: int
    smtp_host: str
    sender_email: str
    debug: bool = False


def load_config() -> Config:
    """Read settings from SHOPLITE_* environment variables, with development defaults."""
    return Config(
        database_path=os.environ.get("SHOPLITE_DB", "shoplite.db"),
        secret_key=os.environ.get("SHOPLITE_SECRET", "dev-secret-change-me"),
        tax_rate=float(os.environ.get("SHOPLITE_TAX_RATE", "0.08")),
        low_stock_threshold=int(os.environ.get("SHOPLITE_LOW_STOCK", "5")),
        smtp_host=os.environ.get("SHOPLITE_SMTP_HOST", "localhost"),
        sender_email=os.environ.get("SHOPLITE_SENDER", "orders@shoplite.local"),
        debug=os.environ.get("SHOPLITE_DEBUG", "0") == "1",
    )

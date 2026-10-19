"""Customer e-mails about orders."""
import logging
import smtplib
from email.message import EmailMessage

from models import Order

log = logging.getLogger(__name__)


def _send_email(smtp_host: str, sender: str, to: str, subject: str, body: str) -> bool:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, timeout=10) as smtp:
            smtp.send_message(message)
        return True
    except OSError as exc:  # never fail an order just because e-mail is down
        log.error("Could not send e-mail to %s: %s", to, exc)
        return False


def send_order_confirmation(order: Order, smtp_host: str, sender: str) -> bool:
    """E-mail the customer a summary of the order they just placed."""
    lines = [f"- {item.quantity} x product #{item.product_id}" for item in order.items]
    body = (
        f"Thanks for your order #{order.id}!\n\n"
        + "\n".join(lines)
        + f"\n\nTotal: ${order.total_cents / 100:.2f}"
    )
    return _send_email(smtp_host, sender, order.customer_email, f"Order #{order.id} confirmed", body)


def send_shipping_notice(order: Order, smtp_host: str, sender: str) -> bool:
    """Tell the customer their order has left the warehouse."""
    body = f"Good news! Order #{order.id} is on its way."
    return _send_email(smtp_host, sender, order.customer_email, f"Order #{order.id} shipped", body)

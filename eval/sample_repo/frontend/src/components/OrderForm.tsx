import { useEffect, useState } from "react";
import { ApiError, fetchProducts, placeOrder, type Product } from "../api";
import { Cart, loadCart, saveCart } from "../cart";

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

/** Checkout form: pick products, enter an e-mail and an optional discount code, then place the order. */
export default function OrderForm() {
  const [products, setProducts] = useState<Product[]>([]);
  const [cart, setCart] = useState(() => new Cart());
  const [email, setEmail] = useState("");
  const [discountCode, setDiscountCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    fetchProducts().then((list) => {
      setProducts(list);
      setCart(loadCart(list));
    });
  }, []);

  function handleAdd(product: Product) {
    cart.addItem(product);
    saveCart(cart);
    setCart(Object.assign(new Cart(), cart));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (cart.isEmpty) {
      setMessage("Your cart is empty.");
      return;
    }
    try {
      const order = await placeOrder(email, cart.toOrderLines(), discountCode);
      setMessage(`Order #${order.id} placed. Total charged: ${formatCents(order.total_cents)}`);
      localStorage.removeItem("shoplite.cart");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Something went wrong.");
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <ul>
        {products.map((product) => (
          <li key={product.id}>
            {product.name} — {formatCents(product.price_cents)} ({product.stock} left)
            <button type="button" onClick={() => handleAdd(product)}>
              Add
            </button>
          </li>
        ))}
      </ul>
      <p>Subtotal: {formatCents(cart.subtotalCents())}</p>
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required />
      <input value={discountCode} onChange={(e) => setDiscountCode(e.target.value)} placeholder="Discount code" />
      <button type="submit">Place order</button>
      {message && <p role="status">{message}</p>}
    </form>
  );
}

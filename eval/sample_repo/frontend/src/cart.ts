import type { OrderLine, Product } from "./api";

const STORAGE_KEY = "shoplite.cart";

export interface CartLine {
  product: Product;
  quantity: number;
}

/** Client-side shopping cart kept in memory and mirrored to localStorage. */
export class Cart {
  private lines = new Map<number, CartLine>();

  addItem(product: Product, quantity = 1): void {
    const existing = this.lines.get(product.id);
    const nextQuantity = (existing?.quantity ?? 0) + quantity;
    if (nextQuantity > product.stock) {
      throw new Error(`Only ${product.stock} of ${product.name} left`);
    }
    this.lines.set(product.id, { product, quantity: nextQuantity });
  }

  removeItem(productId: number): void {
    this.lines.delete(productId);
  }

  /** Sum of price x quantity in cents, before discounts, tax and shipping. */
  subtotalCents(): number {
    let total = 0;
    for (const line of this.lines.values()) {
      total += line.product.price_cents * line.quantity;
    }
    return total;
  }

  toOrderLines(): OrderLine[] {
    return [...this.lines.values()].map((line) => ({ product_id: line.product.id, quantity: line.quantity }));
  }

  get isEmpty(): boolean {
    return this.lines.size === 0;
  }
}

/** Persist the cart so it survives page reloads. */
export function saveCart(cart: Cart): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cart.toOrderLines()));
}

export function loadCart(products: Product[]): Cart {
  const cart = new Cart();
  const saved: OrderLine[] = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  for (const line of saved) {
    const product = products.find((p) => p.id === line.product_id);
    if (product) {
      cart.addItem(product, line.quantity);
    }
  }
  return cart;
}

// Thin wrappers around the ShopLite REST API.
export interface Product {
  id: number;
  name: string;
  price_cents: number;
  stock: number;
}

export interface OrderLine {
  product_id: number;
  quantity: number;
}

export interface PlacedOrder {
  id: number;
  status: string;
  total_cents: number;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const BASE_URL = "/api";

/** Shared fetch helper: sends JSON and throws ApiError with the server's message on failure. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await response.json();
  if (!response.ok) {
    throw new ApiError(response.status, body.error ?? "Request failed");
  }
  return body as T;
}

export function fetchProducts(): Promise<Product[]> {
  return request<Product[]>("/products");
}

/** Submit the checkout: POST /api/orders with the e-mail, cart lines and discount code. */
export function placeOrder(email: string, items: OrderLine[], discountCode?: string): Promise<PlacedOrder> {
  return request<PlacedOrder>("/orders", {
    method: "POST",
    body: JSON.stringify({ email, items, discount_code: discountCode || null }),
  });
}

export function cancelOrder(orderId: number): Promise<PlacedOrder> {
  return request<PlacedOrder>(`/orders/${orderId}/cancel`, { method: "POST" });
}

"""
Demo sample-database generator.

Builds a tiny, self-contained SQLite e-commerce database that exercises every
amnesic feature — multi-table FK graph, enum column, searchable text fields,
multi-row data for db_query. Used by `amnesic init --demo` so a visitor can
try amnesic end-to-end without configuring any credentials.

The schema is deliberately small (under 50 KB on disk) and deterministic
(seeded RNG) so the demo is reproducible across machines.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT,
    signup_date TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    price_cents  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    status       INTEGER NOT NULL,
    placed_at    TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id                INTEGER PRIMARY KEY,
    order_id          INTEGER NOT NULL REFERENCES orders(id),
    product_id        INTEGER NOT NULL REFERENCES products(id),
    quantity          INTEGER NOT NULL,
    unit_price_cents  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_items_order    ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_items_product  ON order_items(product_id);
"""

# status is the showcase enum — its meaning isn't obvious from the integer
# alone, which is exactly the kind of thing db_annotate is designed to solve.
_ORDER_STATUSES = [1, 2, 3, 4, 5]  # 1=pending 2=paid 3=shipped 4=delivered 5=cancelled

_FIRST_NAMES = [
    "Amelia", "Noah", "Priya", "Liam", "Sofia", "Wei", "Aisha", "Mateo",
    "Yuki", "Ravi", "Chloe", "Omar", "Zara", "Diego", "Anya", "Kenji",
    "Lara", "Idris", "Mira", "Tomas",
]
_LAST_NAMES = [
    "Patel", "Garcia", "Chen", "Khan", "Rossi", "Singh", "Nakamura", "Silva",
    "Okafor", "Müller", "Costa", "Kowalski", "Tanaka", "Adeyemi", "Lopez",
    "Eriksen",
]

_PRODUCT_CATALOG = [
    # (name, category, price_cents)
    ("Wireless Headphones",       "Electronics", 7999),
    ("Mechanical Keyboard",       "Electronics", 12999),
    ("USB-C Hub",                 "Electronics", 3499),
    ("Smart Mug Warmer",          "Electronics", 5499),
    ("Webcam 1080p",              "Electronics", 4299),
    ("Standing Desk Mat",         "Furniture",   6499),
    ("Ergonomic Chair Cushion",   "Furniture",   2999),
    ("Monitor Arm",               "Furniture",   8999),
    ("Cable Management Tray",     "Furniture",   1899),
    ("Desk Lamp LED",             "Furniture",   3299),
    ("Espresso Beans 1kg",        "Grocery",     2499),
    ("Loose-Leaf Tea Sampler",    "Grocery",     1899),
    ("Dark Chocolate 70%",        "Grocery",      599),
    ("Olive Oil 500ml",           "Grocery",     1499),
    ("Sea Salt Flakes",           "Grocery",      799),
    ("Notebook A5 Dotted",        "Stationery",   899),
    ("Fountain Pen Starter",      "Stationery",  3499),
    ("Pencil Set HB",             "Stationery",   349),
    ("Sticky Notes Pack",         "Stationery",   249),
    ("Ruler 30cm Steel",          "Stationery",   449),
    ("Running Shoes",             "Apparel",    11999),
    ("Cotton T-Shirt",            "Apparel",     1999),
    ("Wool Socks 3-Pack",         "Apparel",     1599),
    ("Rain Jacket Lightweight",   "Apparel",     8999),
    ("Beanie Knit",               "Apparel",     1299),
    ("Yoga Mat",                  "Fitness",     3999),
    ("Resistance Bands Set",      "Fitness",     1799),
    ("Foam Roller",               "Fitness",     2499),
    ("Water Bottle 1L",           "Fitness",     1299),
    ("Jump Rope",                 "Fitness",      899),
]


def build_demo_db(path: Path | str, *, seed: int = 42) -> Path:
    """
    Create a fresh demo SQLite database at the given path.

    Overwrites any existing file at the path so re-running `init --demo` always
    yields a known-good starting state. Returns the resolved path.
    """
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(seed)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)

        # --- customers ---
        customers: list[tuple] = []
        for i in range(1, 21):
            first = rng.choice(_FIRST_NAMES)
            last = rng.choice(_LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}@example.com"
            # spread signups across 2024-2025 deterministically
            month = ((i * 7) % 12) + 1
            day = ((i * 13) % 27) + 1
            year = 2024 + (i % 2)
            signup = f"{year:04d}-{month:02d}-{day:02d}"
            customers.append((i, f"{first} {last}", email, signup))
        conn.executemany(
            "INSERT INTO customers (id, name, email, signup_date) VALUES (?, ?, ?, ?)",
            customers,
        )

        # --- products ---
        products = [
            (i + 1, name, category, price)
            for i, (name, category, price) in enumerate(_PRODUCT_CATALOG)
        ]
        conn.executemany(
            "INSERT INTO products (id, name, category, price_cents) VALUES (?, ?, ?, ?)",
            products,
        )

        # --- orders + items ---
        orders: list[tuple] = []
        items: list[tuple] = []
        order_id = 0
        item_id = 0
        for _ in range(100):
            order_id += 1
            cust = rng.randint(1, 20)
            status = rng.choice(_ORDER_STATUSES)
            month = rng.randint(1, 12)
            day = rng.randint(1, 28)
            placed = f"2025-{month:02d}-{day:02d}"
            orders.append((order_id, cust, status, placed))

            # 1–4 line items per order, no duplicate product within an order
            n_items = rng.randint(1, 4)
            chosen_products = rng.sample(range(1, len(_PRODUCT_CATALOG) + 1), n_items)
            for pid in chosen_products:
                item_id += 1
                qty = rng.randint(1, 3)
                unit_price = _PRODUCT_CATALOG[pid - 1][2]
                items.append((item_id, order_id, pid, qty, unit_price))

        conn.executemany(
            "INSERT INTO orders (id, customer_id, status, placed_at) VALUES (?, ?, ?, ?)",
            orders,
        )
        conn.executemany(
            "INSERT INTO order_items "
            "(id, order_id, product_id, quantity, unit_price_cents) "
            "VALUES (?, ?, ?, ?, ?)",
            items,
        )
        conn.commit()
    finally:
        conn.close()

    return db_path

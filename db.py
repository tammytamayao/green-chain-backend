# db.py
import sqlite3
from config import DB_PATH

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # ✅ SQLite foreign keys are OFF by default
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ---------- USERS ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('farmer','disposer','driver','admin','consumer')),

            -- farmer
            farm_name TEXT,
            farm_location TEXT,

            -- disposer
            business TEXT,
            location TEXT,

            -- driver
            license_id TEXT,

            -- admin
            email TEXT,
            organization TEXT,

            -- consumer
            address TEXT,

            created_at INTEGER NOT NULL
        );
        """
    )

    # ---------- PRODUCTS ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            variant TEXT NOT NULL,
            current_price REAL
        );
        """
    )

    # ---------- STALLS ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stalls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stall_name TEXT NOT NULL,
            stall_location TEXT NOT NULL,
            representative TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    # ---------- VEHICLES ----------
    # NOTE: keeping column name "class" to match your existing code.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            class TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    # ---------- SUPPLIES ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS supplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weight REAL NOT NULL,
            farmer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            FOREIGN KEY(farmer_id) REFERENCES users(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )

    # ---------- DEMANDS ----------
    # ✅ Now has status (open/completed) and is NOT deleted when completed.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS demands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weight REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            stall_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            FOREIGN KEY(stall_id) REFERENCES stalls(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )

    # ---------- STALL INVENTORY ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stall_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stocks REAL NOT NULL DEFAULT 0,
            size TEXT NOT NULL,
            type TEXT NOT NULL,
            freshness TEXT NOT NULL,
            class TEXT NOT NULL,
            price REAL,
            product_id INTEGER NOT NULL,
            stall_id INTEGER NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(stall_id) REFERENCES stalls(id),
            UNIQUE(stall_id, product_id, size, type)
        );
        """
    )

    # ---------- REQUESTS ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price REAL NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            supply_id INTEGER NOT NULL,
            demand_id INTEGER NOT NULL,
            FOREIGN KEY(supply_id) REFERENCES supplies(id),
            FOREIGN KEY(demand_id) REFERENCES demands(id)
        );
        """
    )

    # ---------- ORDERS ----------
    # ✅ Removed delivery_id to avoid circular FK.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            weight REAL NOT NULL,
            stall_inventory_id INTEGER NOT NULL,
            consumer_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            FOREIGN KEY(stall_inventory_id) REFERENCES stall_inventory(id),
            FOREIGN KEY(consumer_id) REFERENCES users(id)
        );
        """
    )

    # ---------- DELIVERIES ----------
    # ✅ Created only for an order OR a request.
    # ✅ Unassigned initially: driver_id NULL, vehicle_id NULL, status='unassigned'
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,

            driver_id INTEGER,
            vehicle_id INTEGER,

            order_id INTEGER,
            request_id INTEGER,

            status TEXT NOT NULL DEFAULT 'unassigned'
              CHECK (status IN ('unassigned','assigned','picked_up','in_transit','delivered','cancelled')),

            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            assigned_at INTEGER,
            picked_up_at INTEGER,
            delivered_at INTEGER,

            FOREIGN KEY(driver_id) REFERENCES users(id),
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id),
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(request_id) REFERENCES requests(id),

            CHECK (
              (order_id IS NOT NULL AND request_id IS NULL)
              OR
              (order_id IS NULL AND request_id IS NOT NULL)
            )
        );
        """
    )

    # ---------- FEEDBACKS ----------
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notes TEXT NOT NULL,
            attachment TEXT,
            rating INTEGER,
            order_id INTEGER,
            request_id INTEGER,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(request_id) REFERENCES requests(id)
        );
        """
    )

    conn.commit()
    conn.close()

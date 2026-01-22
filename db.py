# db.py
import sqlite3
import time
from config import DB_PATH

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Users table (includes all role-specific optional columns)
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

    # Vehicles (only for drivers, owned by a user)
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

    # Products: id, name, variant, current_price
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

    # Supplies: id, weight, farmer_id (FK), product_id (FK)
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

    # Demands: id, weight, stall_id (FK), product_id (FK)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS demands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weight REAL NOT NULL,
            stall_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            FOREIGN KEY(stall_id) REFERENCES stalls(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """
    )

    # Requests: id, price, method, supply_id (FK), demand_id (FK)
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

    # Stalls (owned by disposers; user_id should refer to a disposer user)
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

    # Stall inventory:
    # id, stocks, size, type, freshness, class, product_id (FK), stall_id (FK)
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

    # Orders: id, amount, method, status, delivery_id, stall_inventory_id, consumer_id
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            weight REAL,
            delivery_id INTEGER,
            stall_inventory_id INTEGER NOT NULL,
            consumer_id INTEGER NOT NULL,
            FOREIGN KEY(delivery_id) REFERENCES deliveries(id),
            FOREIGN KEY(stall_inventory_id) REFERENCES stall_inventory(id),
            FOREIGN KEY(consumer_id) REFERENCES users(id)
        );
        """
    )

    # Feedbacks: id, notes, attachment, rating, order_id, request_id
    # Either order_id or request_id (or both) can be NULL
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

    # Deliveries: id, origin, destination, vehicle_id, order_id, request_id
    # Either order_id or request_id (or both) can be NULL
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            vehicle_id INTEGER NOT NULL,
            order_id INTEGER,
            request_id INTEGER,
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id),
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(request_id) REFERENCES requests(id)
        );
        """
    )

    conn.commit()
    conn.close()

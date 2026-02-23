# routes/deliveries.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user
import time

deliveries_bp = Blueprint("deliveries", __name__, url_prefix="/deliveries")

ALLOWED_STATUS = (
    "unassigned",
    "assigned",
    "picked_up",
    "in_transit",
    "delivered",
    "cancelled",
)

# ✅ Joined SELECT so we can include weight + price for BOTH order-deliveries and request-deliveries
DELIVERY_SELECT = """
SELECT
    d.id,
    d.origin,
    d.destination,
    d.driver_id,
    d.vehicle_id,
    d.order_id,
    d.request_id,
    d.status,
    d.created_at,
    d.assigned_at,
    d.picked_up_at,
    d.delivered_at,

    -- ✅ computed fields
    CASE
      WHEN d.order_id IS NOT NULL THEN 'order'
      ELSE 'request'
    END AS kind,

    CASE
      WHEN d.order_id IS NOT NULL THEN o.weight
      ELSE s.weight
    END AS weight,

    CASE
      WHEN d.order_id IS NOT NULL THEN o.amount
      ELSE r.price
    END AS price

FROM deliveries d
LEFT JOIN orders   o ON o.id = d.order_id
LEFT JOIN requests r ON r.id = d.request_id
LEFT JOIN supplies s ON s.id = r.supply_id
"""


# ---------- Helpers ----------

def _require_user(req):
    user_id, _ = auth_user(req)
    if not user_id:
        return None, (jsonify({"error": "unauthorized"}), 401)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, type, address FROM users WHERE id = ?;", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, (jsonify({"error": "user not found"}), 404)

    return (row, conn), None


def _require_driver(req):
    ctx, err = _require_user(req)
    if err:
        return None, err
    (user_row, conn) = ctx
    if user_row["type"] != "driver":
        conn.close()
        return None, (jsonify({"error": "forbidden, driver only"}), 403)
    return (user_row, conn), None


def _require_admin(req):
    ctx, err = _require_user(req)
    if err:
        return None, err
    (user_row, conn) = ctx
    if user_row["type"] != "admin":
        conn.close()
        return None, (jsonify({"error": "forbidden, admin only"}), 403)
    return (user_row, conn), None


def _delivery_row_to_dict(row):
    return {
        "id": row["id"],
        "origin": row["origin"],
        "destination": row["destination"],
        "driver_id": row["driver_id"],
        "vehicle_id": row["vehicle_id"],
        "order_id": row["order_id"],
        "request_id": row["request_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "assigned_at": row["assigned_at"],
        "picked_up_at": row["picked_up_at"],
        "delivered_at": row["delivered_at"],

        # ✅ NEW
        "kind": row["kind"],     # "order" | "request"
        "weight": row["weight"], # number (from orders.weight OR supplies.weight)
        "price": row["price"],   # number (from orders.amount OR requests.price)
    }


# ---------- Internal creation helpers (used by orders/requests) ----------

def create_delivery_for_order(cur, order_id: int):
    """
    Create an UNASSIGNED delivery for a given order.
    - origin: stall_location
    - destination: consumer address
    - order_id set, request_id NULL
    Returns dict, or None if missing data.
    DOES NOT COMMIT.
    """
    # Get stall location + consumer address
    cur.execute(
        """
        SELECT
            o.id AS order_id,
            u.address AS consumer_address,
            s.stall_location AS stall_location
        FROM orders o
        JOIN users u ON o.consumer_id = u.id
        JOIN stall_inventory si ON o.stall_inventory_id = si.id
        JOIN stalls s ON si.stall_id = s.id
        WHERE o.id = ?;
        """,
        (order_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    if not row["consumer_address"]:
        return None

    origin = row["stall_location"] or "Unknown origin"
    destination = row["consumer_address"]

    now = int(time.time())
    cur.execute(
        """
        INSERT INTO deliveries (
            origin, destination,
            driver_id, vehicle_id,
            order_id, request_id,
            status, created_at
        ) VALUES (?, ?, NULL, NULL, ?, NULL, 'unassigned', ?);
        """,
        (origin, destination, int(order_id), now),
    )
    delivery_id = cur.lastrowid

    # ✅ fetch using joined select so response contains weight + price too
    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    drow = cur.fetchone()
    return _delivery_row_to_dict(drow) if drow else None


def create_delivery_for_request(cur, request_id: int):
    """
    Create an UNASSIGNED delivery for a given request.
    - origin: farm_location (or farm_name)
    - destination: stall_location
    - request_id set, order_id NULL
    Returns dict or None if missing data.
    DOES NOT COMMIT.
    """
    cur.execute(
        """
        SELECT
            r.id AS request_id,
            uf.farm_location AS farm_location,
            uf.farm_name AS farm_name,
            st.stall_location AS stall_location
        FROM requests r
        JOIN supplies s ON r.supply_id = s.id
        JOIN users uf ON s.farmer_id = uf.id
        JOIN demands d ON r.demand_id = d.id
        JOIN stalls st ON d.stall_id = st.id
        WHERE r.id = ?;
        """,
        (request_id,),
    )
    row = cur.fetchone()
    if not row:
        return None

    origin = row["farm_location"] or row["farm_name"] or "Unknown origin"
    destination = row["stall_location"] or "Unknown destination"

    now = int(time.time())
    cur.execute(
        """
        INSERT INTO deliveries (
            origin, destination,
            driver_id, vehicle_id,
            order_id, request_id,
            status, created_at
        ) VALUES (?, ?, NULL, NULL, NULL, ?, 'unassigned', ?);
        """,
        (origin, destination, int(request_id), now),
    )
    delivery_id = cur.lastrowid

    # ✅ fetch using joined select so response contains weight + price too
    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    drow = cur.fetchone()
    return _delivery_row_to_dict(drow) if drow else None


# ---------- CRUD + Driver actions ----------

@deliveries_bp.get("")
def list_deliveries():
    """
    GET /deliveries

    Behavior by user type:
    - driver: can list
        - ?scope=unassigned (default): deliveries with status=unassigned
        - ?scope=mine: deliveries where driver_id = current user
        - optional ?status=...
    - admin: can list all (optional ?status=...)
    - others: forbidden
    """
    ctx, err = _require_user(request)
    if err:
        return err
    (user_row, conn) = ctx
    cur = conn.cursor()

    status = (request.args.get("status") or "").strip().lower()
    scope = (request.args.get("scope") or "").strip().lower()

    if user_row["type"] == "driver":
        if not scope:
            scope = "unassigned"

        where = []
        params = []

        if scope == "unassigned":
            where.append("d.status = 'unassigned'")
        elif scope == "mine":
            where.append("d.driver_id = ?")
            params.append(user_row["id"])
        else:
            conn.close()
            return jsonify({"error": "invalid scope (use 'unassigned' or 'mine')"}), 400

        if status:
            if status not in ALLOWED_STATUS:
                conn.close()
                return jsonify({"error": f"status must be one of {', '.join(ALLOWED_STATUS)}"}), 400
            where.append("d.status = ?")
            params.append(status)

        q = DELIVERY_SELECT
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY d.id DESC;"

        cur.execute(q, tuple(params))
        rows = cur.fetchall()
        conn.close()
        return jsonify([_delivery_row_to_dict(r) for r in rows]), 200

    if user_row["type"] == "admin":
        where = []
        params = []
        if status:
            if status not in ALLOWED_STATUS:
                conn.close()
                return jsonify({"error": f"status must be one of {', '.join(ALLOWED_STATUS)}"}), 400
            where.append("d.status = ?")
            params.append(status)

        q = DELIVERY_SELECT
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY d.id DESC;"

        cur.execute(q, tuple(params))
        rows = cur.fetchall()
        conn.close()
        return jsonify([_delivery_row_to_dict(r) for r in rows]), 200

    conn.close()
    return jsonify({"error": "forbidden"}), 403


@deliveries_bp.get("/<int:delivery_id>")
def get_delivery(delivery_id):
    """
    GET /deliveries/<id>
    - driver: can view if unassigned OR assigned to them
    - admin: can view any
    """
    ctx, err = _require_user(request)
    if err:
        return err
    (user_row, conn) = ctx
    cur = conn.cursor()

    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "delivery not found"}), 404

    if user_row["type"] == "admin":
        conn.close()
        return jsonify(_delivery_row_to_dict(row)), 200

    if user_row["type"] == "driver":
        # allow if unassigned OR mine
        if row["status"] == "unassigned" or row["driver_id"] == user_row["id"]:
            conn.close()
            return jsonify(_delivery_row_to_dict(row)), 200
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    conn.close()
    return jsonify({"error": "forbidden"}), 403


@deliveries_bp.post("")
def create_delivery_manual():
    """
    POST /deliveries
    Admin-only manual creation (optional use).

    Body:
    {
      "origin": "...",
      "destination": "...",
      "order_id": 123   # OR "request_id": 55 (exactly one)
    }
    """
    ctx, err = _require_admin(request)
    if err:
        return err
    (_admin_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    origin = (data.get("origin") or "").strip()
    destination = (data.get("destination") or "").strip()
    order_id = data.get("order_id")
    request_id = data.get("request_id")

    if not origin or not destination:
        conn.close()
        return jsonify({"error": "origin and destination are required"}), 400

    if (order_id is None and request_id is None) or (order_id is not None and request_id is not None):
        conn.close()
        return jsonify({"error": "provide exactly one of order_id or request_id"}), 400

    # validate existence
    if order_id is not None:
        cur.execute("SELECT id FROM orders WHERE id = ?;", (order_id,))
        if not cur.fetchone():
            conn.close()
            return jsonify({"error": "order not found"}), 404

    if request_id is not None:
        cur.execute("SELECT id FROM requests WHERE id = ?;", (request_id,))
        if not cur.fetchone():
            conn.close()
            return jsonify({"error": "request not found"}), 404

    now = int(time.time())
    cur.execute(
        """
        INSERT INTO deliveries (
            origin, destination, driver_id, vehicle_id,
            order_id, request_id, status, created_at
        ) VALUES (?, ?, NULL, NULL, ?, ?, 'unassigned', ?);
        """,
        (origin, destination, order_id, request_id, now),
    )
    delivery_id = cur.lastrowid

    # ✅ return joined payload
    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    row = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_delivery_row_to_dict(row)), 201


@deliveries_bp.patch("/<int:delivery_id>/assign")
def assign_delivery(delivery_id):
    """
    PATCH /deliveries/<id>/assign
    Driver-only: claim an unassigned delivery.

    Body:
    { "vehicle_id": 10 }

    Rules:
    - delivery must be unassigned
    - vehicle must belong to this driver
    - atomically claim (prevents 2 drivers claiming at same time)
    """
    ctx, err = _require_driver(request)
    if err:
        return err
    (driver_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    vehicle_id = data.get("vehicle_id")
    if not vehicle_id:
        conn.close()
        return jsonify({"error": "vehicle_id is required"}), 400

    # ensure vehicle belongs to driver
    cur.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?;",
        (vehicle_id, driver_row["id"]),
    )
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "vehicle not found or not yours"}), 404

    now = int(time.time())
    cur.execute(
        """
        UPDATE deliveries
        SET driver_id = ?, vehicle_id = ?, status = 'assigned', assigned_at = ?
        WHERE id = ?
          AND status = 'unassigned'
          AND driver_id IS NULL
          AND vehicle_id IS NULL;
        """,
        (driver_row["id"], vehicle_id, now, delivery_id),
    )

    if cur.rowcount != 1:
        conn.close()
        return jsonify({"error": "delivery already claimed or not unassigned"}), 409

    # ✅ return joined payload
    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    row = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_delivery_row_to_dict(row)), 200


@deliveries_bp.patch("/<int:delivery_id>/status")
def update_delivery_status(delivery_id):
    """
    PATCH /deliveries/<id>/status
    Driver-only: update status for deliveries assigned to them.

    Body:
    { "status": "picked_up" | "in_transit" | "delivered" | "cancelled" }
    """
    ctx, err = _require_driver(request)
    if err:
        return err
    (driver_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if not status:
        conn.close()
        return jsonify({"error": "status is required"}), 400
    if status not in ALLOWED_STATUS:
        conn.close()
        return jsonify({"error": f"status must be one of {', '.join(ALLOWED_STATUS)}"}), 400

    # ensure delivery belongs to this driver
    cur.execute("SELECT * FROM deliveries WHERE id = ?;", (delivery_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "delivery not found"}), 404
    if row["driver_id"] != driver_row["id"]:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    now = int(time.time())
    picked_up_at = row["picked_up_at"]
    delivered_at = row["delivered_at"]

    if status == "picked_up" and not picked_up_at:
        picked_up_at = now
    if status == "delivered" and not delivered_at:
        delivered_at = now

    cur.execute(
        """
        UPDATE deliveries
        SET status = ?,
            picked_up_at = ?,
            delivered_at = ?
        WHERE id = ?;
        """,
        (status, picked_up_at, delivered_at, delivery_id),
    )

    # ✅ NEW: if this is a request-delivery (request_id present, order_id null)
    # and it is completed (delivered), set the request status to "accepted"
    if status == "delivered" and row["request_id"] is not None and row["order_id"] is None:
        cur.execute(
            """
            UPDATE requests
            SET status = 'accepted'
            WHERE id = ?;
            """,
            (row["request_id"],),
        )
        # Optional: if you want to ensure request exists:
        # if cur.rowcount != 1: you could log or return an error

    # return joined payload
    cur.execute(DELIVERY_SELECT + " WHERE d.id = ?;", (delivery_id,))
    updated = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_delivery_row_to_dict(updated)), 200


@deliveries_bp.delete("/<int:delivery_id>")
def delete_delivery(delivery_id):
    """
    DELETE /deliveries/<id>
    Admin-only.
    """
    ctx, err = _require_admin(request)
    if err:
        return err
    (_admin_row, conn) = ctx
    cur = conn.cursor()

    cur.execute("SELECT id FROM deliveries WHERE id = ?;", (delivery_id,))
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "delivery not found"}), 404

    cur.execute("DELETE FROM deliveries WHERE id = ?;", (delivery_id,))
    conn.commit()
    conn.close()
    return ("", 204)

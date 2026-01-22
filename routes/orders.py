from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

ALLOWED_METHODS = ("gcash", "cash")
ALLOWED_STATUS = ("processing", "accepted", "rejected", "completed", "cancelled")


# ---------- Shared helpers ----------

def _require_user(req):
    user_id, _ = auth_user(req)
    if not user_id:
        return None, (jsonify({"error": "unauthorized"}), 401)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, type
        FROM users
        WHERE id = ?;
        """,
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, (jsonify({"error": "user not found"}), 404)

    return (row, conn), None


def _require_consumer(req):
    ctx, error_resp = _require_user(req)
    if error_resp:
        return None, error_resp
    (user_row, conn) = ctx
    if user_row["type"] != "consumer":
        conn.close()
        return None, (jsonify({"error": "forbidden, consumer only"}), 403)
    return (user_row, conn), None


def _require_disposer(req):
    ctx, error_resp = _require_user(req)
    if error_resp:
        return None, error_resp
    (user_row, conn) = ctx
    if user_row["type"] != "disposer":
        conn.close()
        return None, (jsonify({"error": "forbidden, disposer only"}), 403)
    return (user_row, conn), None


def _get_disposer_stall_id(cur, user_id):
    cur.execute(
        """
        SELECT id
        FROM stalls
        WHERE user_id = ?
        ORDER BY id
        LIMIT 1;
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return row["id"] if row else None


# ---------- Rich order select (matches your Flutter ConsumerOrder model) ----------
ORDER_SELECT = """
SELECT
    o.id,
    o.amount,
    o.method,
    o.status,
    o.weight,
    o.delivery_id,
    o.stall_inventory_id,
    o.consumer_id,

    si.stocks,
    si.size,
    si.type,
    si.freshness,
    si.class AS item_class,
    si.price AS variant_price,
    si.product_id,
    si.stall_id,

    p.name AS product_name,
    p.variant AS product_variant,
    p.current_price AS current_price,

    s.stall_name AS stall_name,
    s.stall_location AS stall_location
FROM orders o
JOIN stall_inventory si ON o.stall_inventory_id = si.id
JOIN products p         ON si.product_id = p.id
JOIN stalls   s         ON si.stall_id = s.id
"""


def _order_row_to_dict(row):
    return {
        "id": row["id"],
        "amount": row["amount"],
        "method": row["method"],
        "status": row["status"],
        "weight": row["weight"],  # 👈 NEW

        "delivery_id": row["delivery_id"],
        "stall_inventory_id": row["stall_inventory_id"],
        "consumer_id": row["consumer_id"],

        # inventory (joined)
        "stocks": row["stocks"],
        "size": row["size"],
        "type": row["type"],
        "freshness": row["freshness"],
        "item_class": row["item_class"],
        "variant_price": row["variant_price"],

        # product
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "product_variant": row["product_variant"],
        "current_price": row["current_price"],

        # stall
        "stall_id": row["stall_id"],
        "stall_name": row["stall_name"],
        "stall_location": row["stall_location"],
    }


# ---------- Routes ----------

@orders_bp.post("")
def create_order():
    """
    POST /orders
    Body:
    {
      "stall_inventory_id": 1,
      "amount": 120.0,
      "method": "gcash",   # or "cash"
      "weight": 2.5        # 👈 NEW (kg ordered)
    }

    Behavior:
    - Consumer-only.
    - Ensures stall_inventory exists AND has enough stocks.
    - Inserts order with status='processing'.
    - Optionally deducts stocks (recommended).
    - Returns a rich joined row (matches Flutter model).
    """
    ctx, error_resp = _require_consumer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    stall_inventory_id = data.get("stall_inventory_id")
    amount = data.get("amount")
    method = (data.get("method") or "").strip().lower()
    weight = data.get("weight")  # 👈 NEW

    if not stall_inventory_id or amount is None or not method or weight is None:
        conn.close()
        return jsonify(
            {"error": "stall_inventory_id, amount, method, weight are all required"}
        ), 400

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "amount must be a number"}), 400

    try:
        weight = float(weight)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "weight must be a number"}), 400

    if amount < 0:
        conn.close()
        return jsonify({"error": "amount must be >= 0"}), 400

    if weight <= 0:
        conn.close()
        return jsonify({"error": "weight must be > 0"}), 400

    if method not in ALLOWED_METHODS:
        conn.close()
        return jsonify({"error": "method must be 'gcash' or 'cash'"}), 400

    # Ensure stall_inventory exists + check stocks
    cur.execute(
        "SELECT id, stocks FROM stall_inventory WHERE id = ?;",
        (stall_inventory_id,),
    )
    inv_row = cur.fetchone()
    if not inv_row:
        conn.close()
        return jsonify({"error": "stall_inventory item not found"}), 404

    if float(inv_row["stocks"]) < weight:
        conn.close()
        return jsonify({"error": "weight exceeds available stocks"}), 400

    # Insert order (status defaults to 'processing')
    cur.execute(
        """
        INSERT INTO orders (amount, method, status, weight, stall_inventory_id, consumer_id)
        VALUES (?, ?, 'processing', ?, ?, ?);
        """,
        (amount, method, weight, stall_inventory_id, user_row["id"]),
    )
    order_id = cur.lastrowid

    # OPTIONAL but recommended: deduct ordered weight from stocks
    cur.execute(
        "UPDATE stall_inventory SET stocks = stocks - ? WHERE id = ?;",
        (weight, stall_inventory_id),
    )

    # Fetch rich joined row
    cur.execute(ORDER_SELECT + " WHERE o.id = ?;", (order_id,))
    row = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_order_row_to_dict(row)), 201


@orders_bp.get("")
def list_orders():
    """
    GET /orders

    - For consumers: list orders they created.
    - For disposers: list orders for their stall (via stall_inventory).
    Returns rich joined rows (matches Flutter model).
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "consumer":
        cur.execute(
            ORDER_SELECT + """
            WHERE o.consumer_id = ?
            ORDER BY o.id DESC;
            """,
            (user_row["id"],),
        )

    elif user_type == "disposer":
        stall_id = _get_disposer_stall_id(cur, user_row["id"])
        if stall_id is None:
            conn.close()
            return jsonify([]), 200

        cur.execute(
            ORDER_SELECT + """
            WHERE s.id = ?
            ORDER BY o.id DESC;
            """,
            (stall_id,),
        )
    else:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    rows = cur.fetchall()
    conn.close()
    return jsonify([_order_row_to_dict(r) for r in rows]), 200


@orders_bp.get("/<int:order_id>")
def get_order(order_id):
    """
    GET /orders/<id>

    - Consumer can read if they own the order.
    - Disposer can read if the order belongs to their stall.
    Returns rich joined row (matches Flutter model).
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "consumer":
        cur.execute(
            ORDER_SELECT + """
            WHERE o.id = ?
              AND o.consumer_id = ?;
            """,
            (order_id, user_row["id"]),
        )
    elif user_type == "disposer":
        stall_id = _get_disposer_stall_id(cur, user_row["id"])
        if stall_id is None:
            conn.close()
            return jsonify({"error": "no stall found for disposer"}), 400

        cur.execute(
            ORDER_SELECT + """
            WHERE o.id = ?
              AND s.id = ?;
            """,
            (order_id, stall_id),
        )
    else:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "order not found"}), 404

    return jsonify(_order_row_to_dict(row)), 200


@orders_bp.patch("/<int:order_id>/status")
def update_order_status(order_id):
    """
    PATCH /orders/<id>/status
    Body:
    { "status": "accepted" | "rejected" | "completed" | "processing" | "cancelled" }

    Disposer-only: can change status of orders belonging to their stall.
    Returns rich joined row (matches Flutter model).
    """
    ctx, error_resp = _require_disposer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    stall_id = _get_disposer_stall_id(cur, user_row["id"])
    if stall_id is None:
        conn.close()
        return jsonify({"error": "no stall found for disposer"}), 400

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()

    if not status:
        conn.close()
        return jsonify({"error": "status is required"}), 400

    if status not in ALLOWED_STATUS:
        conn.close()
        return jsonify(
            {"error": f"status must be one of {', '.join(ALLOWED_STATUS)}"}
        ), 400

    # Ensure order belongs to this stall
    cur.execute(
        """
        SELECT o.id
        FROM orders o
        JOIN stall_inventory si ON o.stall_inventory_id = si.id
        JOIN stalls   s         ON si.stall_id = s.id
        WHERE o.id = ?
          AND s.id = ?;
        """,
        (order_id, stall_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "order not found"}), 404

    cur.execute(
        "UPDATE orders SET status = ? WHERE id = ?;",
        (status, order_id),
    )

    # Fetch rich joined row
    cur.execute(ORDER_SELECT + " WHERE o.id = ?;", (order_id,))
    updated = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_order_row_to_dict(updated)), 200


@orders_bp.delete("/<int:order_id>")
def delete_order(order_id):
    """
    DELETE /orders/<id>

    Consumer-only:
      - Can delete their own order while it's still 'processing'.
    NOTE: If you deduct stocks on create_order, you might want to RESTORE stocks here.
    """
    ctx, error_resp = _require_consumer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, consumer_id, status, weight, stall_inventory_id
        FROM orders
        WHERE id = ?;
        """,
        (order_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "order not found"}), 404

    if row["consumer_id"] != user_row["id"]:
        conn.close()
        return jsonify({"error": "forbidden, not your order"}), 403

    if row["status"] != "processing":
        conn.close()
        return jsonify({"error": "only 'processing' orders can be deleted"}), 400

    # OPTIONAL but recommended: restore stocks if you deducted on create
    if row["weight"] is not None and row["stall_inventory_id"] is not None:
        cur.execute(
            "UPDATE stall_inventory SET stocks = stocks + ? WHERE id = ?;",
            (float(row["weight"]), int(row["stall_inventory_id"])),
        )

    cur.execute("DELETE FROM orders WHERE id = ?;", (order_id,))
    conn.commit()
    conn.close()
    return ("", 204)

@orders_bp.patch("/<int:order_id>/receive")
def consumer_receive_order(order_id):
    """
    PATCH /orders/<id>/receive

    Consumer-only:
    - Can mark their own order as completed ONLY if it's currently 'accepted'
    Returns rich joined row (matches Flutter model).
    """
    ctx, error_resp = _require_consumer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    # Ensure order exists and belongs to this consumer
    cur.execute(
        """
        SELECT id, consumer_id, status
        FROM orders
        WHERE id = ?;
        """,
        (order_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "order not found"}), 404

    if row["consumer_id"] != user_row["id"]:
        conn.close()
        return jsonify({"error": "forbidden, not your order"}), 403

    # Only allow accepted -> completed
    if row["status"] != "accepted":
        conn.close()
        return jsonify({"error": "only 'accepted' orders can be marked as completed"}), 400

    # Update to completed
    cur.execute(
        "UPDATE orders SET status = 'completed' WHERE id = ?;",
        (order_id,),
    )

    # Fetch rich joined row
    cur.execute(ORDER_SELECT + " WHERE o.id = ?;", (order_id,))
    updated = cur.fetchone()

    conn.commit()
    conn.close()
    return jsonify(_order_row_to_dict(updated)), 200

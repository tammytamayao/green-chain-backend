# routes/orders.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

ALLOWED_METHODS = ("gcash", "cash")


# ---------- Shared helpers ----------

def _require_user(req):
    """
    Returns ((user_row, conn), None) if authenticated user of any type,
    otherwise (None, (response, status)).
    """
    user_id, _ = auth_user(req)
    if not user_id:
        return None, (jsonify({"error": "unauthorized"}), 401)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, type, first_name, last_name
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


def _get_disposer_stall_id(cur, user_id):
    """
    Returns the first stall.id for this disposer user, or None if none.
    """
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


# ---------- Row mapper ----------

def _order_row_to_dict(row):
    """
    Map joined order + stall_inventory + product + stall to JSON.
    """
    return {
        "id": row["id"],
        "amount": row["amount"],
        "method": row["method"],
        "delivery_id": row["delivery_id"],
        "stall_inventory_id": row["stall_inventory_id"],
        "consumer_id": row["consumer_id"],

        # Stall inventory details
        "size": row["size"],
        "type": row["type"],
        "freshness": row["freshness"],
        "item_class": row["class"],
        "variant_price": row["variant_price"],
        "stocks": row["stocks"],

        # Product info
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "product_variant": row["product_variant"],
        "current_price": row["current_price"],

        # Stall info
        "stall_id": row["stall_id"],
        "stall_name": row["stall_name"],
        "stall_location": row["stall_location"],
    }


_BASE_SELECT = """
    SELECT
        o.id,
        o.amount,
        o.method,
        o.delivery_id,
        o.stall_inventory_id,
        o.consumer_id,

        si.stocks,
        si.size,
        si.type,
        si.freshness,
        si.class,
        si.price AS variant_price,
        si.product_id,
        si.stall_id,

        p.name   AS product_name,
        p.variant AS product_variant,
        p.current_price AS current_price,

        s.stall_name,
        s.stall_location

    FROM orders o
    JOIN stall_inventory si ON o.stall_inventory_id = si.id
    JOIN products p        ON si.product_id = p.id
    JOIN stalls s          ON si.stall_id = s.id
"""


# ---------- CRUD routes ----------

@orders_bp.post("")
def create_order():
    """
    POST /orders
    Body:
    {
      "stall_inventory_id": 123,
      "amount": 150.0,
      "method": "gcash" | "cash"
    }

    Consumer-only:
      - Creates an order for a specific stall_inventory row.
      - (Optional) You can later enforce amount <= available stocks.
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

    if not stall_inventory_id or amount is None or not method:
        conn.close()
        return jsonify(
            {"error": "stall_inventory_id, amount, method are all required"}
        ), 400

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "amount must be a number"}), 400

    if amount <= 0:
        conn.close()
        return jsonify({"error": "amount must be > 0"}), 400

    if method not in ALLOWED_METHODS:
        conn.close()
        return jsonify({"error": "method must be 'gcash' or 'cash'"}), 400

    # Ensure stall_inventory exists
    cur.execute(
        """
        SELECT id
        FROM stall_inventory
        WHERE id = ?;
        """,
        (stall_inventory_id,),
    )
    inv_row = cur.fetchone()
    if not inv_row:
        conn.close()
        return jsonify({"error": "stall inventory item not found"}), 404

    # Insert order
    cur.execute(
        """
        INSERT INTO orders (amount, method, delivery_id, stall_inventory_id, consumer_id)
        VALUES (?, ?, NULL, ?, ?);
        """,
        (amount, method, stall_inventory_id, user_row["id"]),
    )
    order_id = cur.lastrowid

    # Return joined view
    cur.execute(
        _BASE_SELECT + """
        WHERE o.id = ?;
        """,
        (order_id,),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()

    return jsonify(_order_row_to_dict(row)), 201


@orders_bp.get("")
def list_orders():
    """
    GET /orders

    - For consumers: list their own orders.
    - For disposers: (optional) list orders that involve their stall inventory.
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "consumer":
        cur.execute(
            _BASE_SELECT + """
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
            _BASE_SELECT + """
            WHERE si.stall_id = ?
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

    - Consumer can read their own order.
    - Disposer can read orders linked to their stall.
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "consumer":
        cur.execute(
            _BASE_SELECT + """
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
            _BASE_SELECT + """
            WHERE o.id = ?
              AND si.stall_id = ?;
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


@orders_bp.patch("/<int:order_id>")
def update_order(order_id):
    """
    PATCH /orders/<id>
    Body (any subset):
    {
      "amount": 200.0,
      "method": "cash" | "gcash"
    }

    Consumer-only: can update their own orders, for now no status.
    (You can later restrict editing if there's a delivery already, etc.)
    """
    ctx, error_resp = _require_consumer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    # Ensure it's theirs
    cur.execute(
        """
        SELECT id
        FROM orders
        WHERE id = ?
          AND consumer_id = ?;
        """,
        (order_id, user_row["id"]),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "order not found"}), 404

    data = request.get_json(silent=True) or {}
    fields = []
    values = []

    if "amount" in data:
        try:
            amount = float(data["amount"])
        except (ValueError, TypeError):
            conn.close()
            return jsonify({"error": "amount must be a number"}), 400
        if amount <= 0:
            conn.close()
            return jsonify({"error": "amount must be > 0"}), 400
        fields.append("amount = ?")
        values.append(amount)

    if "method" in data:
        method = (data["method"] or "").strip().lower()
        if method not in ALLOWED_METHODS:
            conn.close()
            return jsonify(
                {"error": f"method must be one of {', '.join(ALLOWED_METHODS)}"}
            ), 400
        fields.append("method = ?")
        values.append(method)

    if not fields:
        conn.close()
        return jsonify({"error": "no valid fields to update"}), 400

    values.append(order_id)

    cur.execute(
        f"UPDATE orders SET {', '.join(fields)} WHERE id = ?;",
        values,
    )

    cur.execute(
        _BASE_SELECT + """
        WHERE o.id = ?;
        """,
        (order_id,),
    )
    updated = cur.fetchone()
    conn.commit()
    conn.close()

    return jsonify(_order_row_to_dict(updated)), 200


@orders_bp.delete("/<int:order_id>")
def delete_order(order_id):
    """
    DELETE /orders/<id>

    Consumer-only: delete their own order.
    (You can later restrict this if there's a delivery already.)
    """
    ctx, error_resp = _require_consumer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM orders
        WHERE id = ?
          AND consumer_id = ?;
        """,
        (order_id, user_row["id"]),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "order not found"}), 404

    cur.execute(
        "DELETE FROM orders WHERE id = ?;",
        (order_id,),
    )
    conn.commit()
    conn.close()

    return ("", 204)

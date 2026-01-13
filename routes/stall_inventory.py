# routes/stall_inventory.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

stall_inventory_bp = Blueprint(
    "stall_inventory", __name__, url_prefix="/stall_inventory"
)


def _require_disposer(request):
    """
    Returns ((user_row, conn), None) if authenticated disposer,
    otherwise (None, (response, status)).
    """
    user_id, _ = auth_user(request)
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

    if row["type"] != "disposer":
        conn.close()
        return None, (jsonify({"error": "forbidden, disposer only"}), 403)

    return (row, conn), None


def _get_disposer_stall_id(cur, user_id):
    """
    Returns the first stall.id for this disposer user, or None if none.
    Assumes one stall per disposer for now.
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


def _fetch_inventory_row(cur, inv_id):
    """
    Returns a single inventory row (joined with product + orders count).
    """
    cur.execute(
        """
        SELECT
            si.id,
            si.stocks,
            si.size,
            si.type,
            si.freshness,
            si.class,
            si.price AS variant_price,
            si.product_id,
            si.stall_id,
            p.name AS product_name,
            p.variant AS product_variant,
            p.current_price AS current_price,
            COALESCE(COUNT(o.id), 0) AS orders_count
        FROM stall_inventory si
        JOIN products p ON si.product_id = p.id
        LEFT JOIN orders o ON o.stall_inventory_id = si.id
        WHERE si.id = ?
        GROUP BY si.id;
        """,
        (inv_id,),
    )
    return cur.fetchone()


def _inventory_row_to_dict(row):
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "stall_id": row["stall_id"],
        "product_name": row["product_name"],
        "product_variant": row["product_variant"],
        "current_price": row["current_price"],
        "variant_price": row["variant_price"],
        "stocks": row["stocks"],
        "size": row["size"],
        "type": row["type"],
        "freshness": row["freshness"],
        "class": row["class"],
        "orders_count": row["orders_count"],
    }


@stall_inventory_bp.get("")
def list_stall_inventory():
    """
    GET /stall_inventory

    Returns the current disposer’s stall inventory, with product info,
    per-variant price, and orders count per item.
    """
    ctx, error_resp = _require_disposer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    stall_id = _get_disposer_stall_id(cur, user_row["id"])
    if stall_id is None:
        conn.close()
        return jsonify([]), 200

    cur.execute(
        """
        SELECT
            si.id,
            si.stocks,
            si.size,
            si.type,
            si.freshness,
            si.class,
            si.price AS variant_price,
            si.product_id,
            si.stall_id,
            p.name AS product_name,
            p.variant AS product_variant,
            p.current_price AS current_price,
            COALESCE(COUNT(o.id), 0) AS orders_count
        FROM stall_inventory si
        JOIN products p ON si.product_id = p.id
        LEFT JOIN orders o ON o.stall_inventory_id = si.id
        WHERE si.stall_id = ?
        GROUP BY si.id
        ORDER BY p.name, p.variant;
        """,
        (stall_id,),
    )
    rows = cur.fetchall()
    conn.close()

    return jsonify([_inventory_row_to_dict(r) for r in rows]), 200


@stall_inventory_bp.post("")
def create_stall_inventory():
    """
    POST /stall_inventory
    Body:
    {
      "product_id": 1,
      "stocks": 120.0,
      "size": "Big",
      "type": "Organic",
      "freshness": "Newly harvested",
      "class": "A",
      "price": 52.5        # optional, per-variant price
    }

    Creates a new inventory item for the disposer’s stall.
    Allows same product multiple times as long as (size, type) differ.
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
    product_id = data.get("product_id")
    stocks = data.get("stocks")
    size = (data.get("size") or "").strip()
    _type = (data.get("type") or "").strip()
    freshness = (data.get("freshness") or "").strip()
    klass = (data.get("class") or "").strip()
    price = data.get("price", None)

    if not product_id or stocks is None or not size or not _type or not freshness or not klass:
        conn.close()
        return (
            jsonify(
                {
                    "error": (
                        "product_id, stocks, size, type, freshness, class "
                        "are required"
                    )
                }
            ),
            400,
        )

    try:
        stocks = float(stocks)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "stocks must be a number"}), 400

    if stocks <= 0:
        conn.close()
        return jsonify({"error": "stocks must be > 0"}), 400

    if price is not None:
        try:
            price = float(price)
        except (ValueError, TypeError):
            conn.close()
            return jsonify({"error": "price must be a number"}), 400
        if price < 0:
            conn.close()
            return jsonify({"error": "price must be >= 0"}), 400

    # Ensure product exists
    cur.execute(
        "SELECT id FROM products WHERE id = ?;",
        (product_id,),
    )
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "product not found"}), 404

    # Ensure we don't duplicate same (stall, product, size, type)
    cur.execute(
        """
        SELECT id
        FROM stall_inventory
        WHERE stall_id = ?
          AND product_id = ?
          AND size = ?
          AND type = ?;
        """,
        (stall_id, product_id, size, _type),
    )
    if cur.fetchone():
        conn.close()
        return (
            jsonify(
                {
                    "error": (
                        "inventory for this product with the same size and type "
                        "already exists"
                    )
                }
            ),
            400,
        )

    cur.execute(
        """
        INSERT INTO stall_inventory (
          stocks, size, type, freshness, class, price, product_id, stall_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (stocks, size, _type, freshness, klass, price, product_id, stall_id),
    )
    conn.commit()
    inv_id = cur.lastrowid

    row = _fetch_inventory_row(cur, inv_id)
    conn.close()

    return jsonify(_inventory_row_to_dict(row)), 201


@stall_inventory_bp.patch("/<int:inv_id>")
def update_stall_inventory(inv_id):
    """
    PATCH /stall_inventory/<id>
    Body can contain any subset of:
    {
      "stocks": 100.0,
      "size": "Small",
      "type": "Non-organic",
      "freshness": "1 day old",
      "class": "B",
      "price": 60.0
    }

    Also enforces uniqueness of (stall_id, product_id, size, type).
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

    # Ensure inventory belongs to this stall and fetch product/size/type
    cur.execute(
        """
        SELECT id, stall_id, product_id, size, type
        FROM stall_inventory
        WHERE id = ?;
        """,
        (inv_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "inventory item not found"}), 404
    if row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    fields = []
    values = []

    if "stocks" in data:
        try:
            stocks = float(data["stocks"])
        except (ValueError, TypeError):
            conn.close()
            return jsonify({"error": "stocks must be a number"}), 400
        if stocks <= 0:
            conn.close()
            return jsonify({"error": "stocks must be > 0"}), 400
        fields.append("stocks = ?")
        values.append(stocks)

    if "price" in data:
        price = data["price"]
        if price is not None:
            try:
                price = float(price)
            except (ValueError, TypeError):
                conn.close()
                return jsonify({"error": "price must be a number"}), 400
            if price < 0:
                conn.close()
                return jsonify({"error": "price must be >= 0"}), 400
        fields.append("price = ?")
        values.append(price)

    for key in ("size", "type", "freshness", "class"):
        if key in data:
            val = (data[key] or "").strip()
            if not val:
                conn.close()
                return jsonify({"error": f"{key} cannot be empty"}), 400
            fields.append(f"{key} = ?")
            values.append(val)

    if not fields:
        conn.close()
        return jsonify({"error": "no valid fields to update"}), 400

    # Compute resulting size/type to enforce uniqueness
    new_size = (
        (data.get("size") or "").strip()
        if "size" in data
        else row["size"]
    )
    new_type = (
        (data.get("type") or "").strip()
        if "type" in data
        else row["type"]
    )

    # Check uniqueness for (stall, product, size, type) against other rows
    cur.execute(
        """
        SELECT id
        FROM stall_inventory
        WHERE stall_id = ?
          AND product_id = ?
          AND size = ?
          AND type = ?
          AND id != ?;
        """,
        (stall_id, row["product_id"], new_size, new_type, inv_id),
    )
    conflict = cur.fetchone()
    if conflict:
        conn.close()
        return (
            jsonify(
                {
                    "error": (
                        "another inventory item with this product, size and type "
                        "already exists"
                    )
                }
            ),
            400,
        )

    values.append(inv_id)

    cur.execute(
        f"UPDATE stall_inventory SET {', '.join(fields)} WHERE id = ?;",
        values,
    )
    conn.commit()

    updated = _fetch_inventory_row(cur, inv_id)
    conn.close()

    return jsonify(_inventory_row_to_dict(updated)), 200


@stall_inventory_bp.delete("/<int:inv_id>")
def delete_stall_inventory(inv_id):
    """
    DELETE /stall_inventory/<id>
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

    # Ensure inventory belongs to this stall
    cur.execute(
        """
        SELECT id, stall_id
        FROM stall_inventory
        WHERE id = ?;
        """,
        (inv_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "inventory item not found"}), 404
    if row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    cur.execute(
        "DELETE FROM stall_inventory WHERE id = ?;",
        (inv_id,),
    )
    conn.commit()
    conn.close()

    return ("", 204)

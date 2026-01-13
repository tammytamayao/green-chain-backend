# routes/demands.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

demand_bp = Blueprint("demand", __name__, url_prefix="/demands")


# ---------- Helpers ----------

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


def _demand_row_to_dict(row):
    return {
        "id": row["id"],
        "weight": row["weight"],
        "stall_id": row["stall_id"],
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "product_variant": row["product_variant"],
    }


# ---------- Routes ----------

@demand_bp.get("")
def list_demands():
    """
    GET /demands

    Returns all demand rows for the current disposer’s stall,
    joined with product info.
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
            d.id,
            d.weight,
            d.stall_id,
            d.product_id,
            p.name AS product_name,
            p.variant AS product_variant
        FROM demands d
        JOIN products p ON d.product_id = p.id
        WHERE d.stall_id = ?
        ORDER BY p.name, p.variant;
        """,
        (stall_id,),
    )
    rows = cur.fetchall()
    conn.close()

    return jsonify([_demand_row_to_dict(r) for r in rows]), 200


@demand_bp.post("")
def create_or_update_demand():
    """
    POST /demands
    Body:
    {
      "product_id": 1,
      "weight": 50.0
    }

    Behavior:
    - If a demand for (stall_id, product_id) already exists, UPDATE its weight.
    - Otherwise, INSERT a new demand row.

    This matches the "Save request" interaction in the Buy tab
    (one active request per product per stall).
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
    weight = data.get("weight")

    if not product_id or weight is None:
        conn.close()
        return jsonify({"error": "product_id and weight are required"}), 400

    try:
        weight = float(weight)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "weight must be a number"}), 400

    if weight <= 0:
        conn.close()
        return jsonify({"error": "weight must be > 0"}), 400

    # Ensure product exists
    cur.execute(
        "SELECT id FROM products WHERE id = ?;",
        (product_id,),
    )
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "product not found"}), 404

    # Check if there is already a demand for this stall + product
    cur.execute(
        """
        SELECT id
        FROM demands
        WHERE stall_id = ?
          AND product_id = ?
        LIMIT 1;
        """,
        (stall_id, product_id),
    )
    row = cur.fetchone()

    if row:
        # Update existing
        demand_id = row["id"]
        cur.execute(
            """
            UPDATE demands
            SET weight = ?
            WHERE id = ?;
            """,
            (weight, demand_id),
        )
        conn.commit()
    else:
        # Insert new
        cur.execute(
            """
            INSERT INTO demands (weight, stall_id, product_id)
            VALUES (?, ?, ?);
            """,
            (weight, stall_id, product_id),
        )
        conn.commit()
        demand_id = cur.lastrowid

    # Fetch row with product info
    cur.execute(
        """
        SELECT
            d.id,
            d.weight,
            d.stall_id,
            d.product_id,
            p.name AS product_name,
            p.variant AS product_variant
        FROM demands d
        JOIN products p ON d.product_id = p.id
        WHERE d.id = ?;
        """,
        (demand_id,),
    )
    out = cur.fetchone()
    conn.close()

    # You can distinguish new vs updated by checking row above,
    # but frontend usually doesn't need that, so return 200 OK.
    return jsonify(_demand_row_to_dict(out)), 200


@demand_bp.get("/<int:demand_id>")
def get_demand(demand_id):
    """
    GET /demands/<id>

    Fetch a single demand row for the current disposer (by stall ownership).
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

    cur.execute(
        """
        SELECT
            d.id,
            d.weight,
            d.stall_id,
            d.product_id,
            p.name AS product_name,
            p.variant AS product_variant
        FROM demands d
        JOIN products p ON d.product_id = p.id
        WHERE d.id = ?
          AND d.stall_id = ?;
        """,
        (demand_id, stall_id),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "demand not found"}), 404

    return jsonify(_demand_row_to_dict(row)), 200


@demand_bp.patch("/<int:demand_id>")
def update_demand(demand_id):
    """
    PATCH /demands/<id>
    Body:
    {
      "weight": 60.0
    }

    For now we only allow changing weight.
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

    # Ensure demand belongs to this stall
    cur.execute(
        """
        SELECT id, stall_id
        FROM demands
        WHERE id = ?;
        """,
        (demand_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "demand not found"}), 404
    if row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    if "weight" not in data:
        conn.close()
        return jsonify({"error": "weight is required to update"}), 400

    try:
        weight = float(data["weight"])
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "weight must be a number"}), 400

    if weight <= 0:
        conn.close()
        return jsonify({"error": "weight must be > 0"}), 400

    cur.execute(
        """
        UPDATE demands
        SET weight = ?
        WHERE id = ?;
        """,
        (weight, demand_id),
    )
    conn.commit()

    # Return updated row
    cur.execute(
        """
        SELECT
            d.id,
            d.weight,
            d.stall_id,
            d.product_id,
            p.name AS product_name,
            p.variant AS product_variant
        FROM demands d
        JOIN products p ON d.product_id = p.id
        WHERE d.id = ?;
        """,
        (demand_id,),
    )
    updated = cur.fetchone()
    conn.close()

    return jsonify(_demand_row_to_dict(updated)), 200


@demand_bp.delete("/<int:demand_id>")
def delete_demand(demand_id):
    """
    DELETE /demands/<id>
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

    # Ensure demand belongs to this stall
    cur.execute(
        """
        SELECT id, stall_id
        FROM demands
        WHERE id = ?;
        """,
        (demand_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "demand not found"}), 404
    if row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    cur.execute(
        "DELETE FROM demands WHERE id = ?;",
        (demand_id,),
    )
    conn.commit()
    conn.close()

    return ("", 204)

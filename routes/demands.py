# routes/demands.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

demand_bp = Blueprint("demand", __name__, url_prefix="/demands")


# ---------- Helpers ----------

def _require_user(request):
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

    return (row, conn), None


def _require_disposer(request):
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
        "status": row["status"],  # ✅ NEW
        "stall_id": row["stall_id"],
        "product_id": row["product_id"],
        "product_name": row["product_name"],
        "product_variant": row["product_variant"],
        "stall_name": row["stall_name"],
        "stall_location": row["stall_location"],
        "current_price": row["current_price"],
        "requests_count": row["requests_count"],
    }


# ---------- Routes ----------

@demand_bp.get("")
def list_demands():
    """
    GET /demands

    For disposers:
      - Returns all OPEN demand rows for the current disposer’s stall.

    For farmers:
      - Returns all OPEN demand rows for all stalls.

    Both are joined with products + stalls, and include:
      - status
      - stall_name
      - stall_location
      - current_price
      - requests_count
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "disposer":
        stall_id = _get_disposer_stall_id(cur, user_row["id"])
        if stall_id is None:
            conn.close()
            return jsonify([]), 200

        cur.execute(
            """
            SELECT
                d.id,
                d.weight,
                d.status,
                d.stall_id,
                d.product_id,
                p.name           AS product_name,
                p.variant        AS product_variant,
                p.current_price  AS current_price,
                s.stall_name     AS stall_name,
                s.stall_location AS stall_location,
                COALESCE(COUNT(r.id), 0) AS requests_count
            FROM demands d
            JOIN products p ON d.product_id = p.id
            JOIN stalls   s ON d.stall_id = s.id
            LEFT JOIN requests r ON r.demand_id = d.id
            WHERE d.stall_id = ?
              AND d.status = 'open'
            GROUP BY d.id
            ORDER BY p.name, p.variant, s.stall_name;
            """,
            (stall_id,),
        )

    elif user_type == "farmer":
        cur.execute(
            """
            SELECT
                d.id,
                d.weight,
                d.status,
                d.stall_id,
                d.product_id,
                p.name           AS product_name,
                p.variant        AS product_variant,
                p.current_price  AS current_price,
                s.stall_name     AS stall_name,
                s.stall_location AS stall_location,
                COALESCE(COUNT(r.id), 0) AS requests_count
            FROM demands d
            JOIN products p ON d.product_id = p.id
            JOIN stalls   s ON d.stall_id = s.id
            LEFT JOIN requests r ON r.demand_id = d.id
            WHERE d.status = 'open'
            GROUP BY d.id
            ORDER BY p.name, p.variant, s.stall_name;
            """
        )

    else:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    rows = cur.fetchall()
    conn.close()
    return jsonify([_demand_row_to_dict(r) for r in rows]), 200


@demand_bp.post("")
def create_or_update_demand():
    """
    POST /demands

    Behavior (NEW):
    - If an OPEN demand exists for (stall_id, product_id) → UPDATE it
    - If last demand was COMPLETED → CREATE NEW ROW (history)
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
    cur.execute("SELECT id FROM products WHERE id = ?;", (product_id,))
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "product not found"}), 404

    import time
    now = int(time.time())

    # 🔥 IMPORTANT CHANGE:
    # Find ONLY OPEN demand (ignore completed history)
    cur.execute(
        """
        SELECT id
        FROM demands
        WHERE stall_id = ?
          AND product_id = ?
          AND status = 'open'
        LIMIT 1;
        """,
        (stall_id, product_id),
    )
    row = cur.fetchone()

    if row:
        # Update existing OPEN demand
        demand_id = row["id"]
        cur.execute(
            """
            UPDATE demands
            SET weight = ?, updated_at = ?
            WHERE id = ?;
            """,
            (weight, now, demand_id),
        )
        conn.commit()

    else:
        # Create NEW demand (history preserved)
        cur.execute(
            """
            INSERT INTO demands (weight, status, stall_id, product_id, created_at, updated_at)
            VALUES (?, 'open', ?, ?, ?, ?);
            """,
            (weight, stall_id, product_id, now, now),
        )
        conn.commit()
        demand_id = cur.lastrowid

    # Fetch joined row (unchanged)
    cur.execute(
        """
        SELECT
            d.id,
            d.weight,
            d.status,
            d.stall_id,
            d.product_id,
            d.created_at,
            d.updated_at,
            p.name           AS product_name,
            p.variant        AS product_variant,
            p.current_price  AS current_price,
            s.stall_name     AS stall_name,
            s.stall_location AS stall_location,
            COALESCE(COUNT(r.id), 0) AS requests_count
        FROM demands d
        JOIN products p ON d.product_id = p.id
        JOIN stalls   s ON d.stall_id = s.id
        LEFT JOIN requests r ON r.demand_id = d.id
        WHERE d.id = ?
        GROUP BY d.id;
        """,
        (demand_id,),
    )

    out = cur.fetchone()
    conn.close()
    return jsonify(_demand_row_to_dict(out)), 200


@demand_bp.get("/<int:demand_id>")
def get_demand(demand_id):
    """
    GET /demands/<id>
    Disposer-only, must belong to disposer stall.
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
            d.status,
            d.stall_id,
            d.product_id,
            p.name           AS product_name,
            p.variant        AS product_variant,
            p.current_price  AS current_price,
            s.stall_name     AS stall_name,
            s.stall_location AS stall_location,
            COALESCE(COUNT(r.id), 0) AS requests_count
        FROM demands d
        JOIN products p ON d.product_id = p.id
        JOIN stalls   s ON d.stall_id = s.id
        LEFT JOIN requests r ON r.demand_id = d.id
        WHERE d.id = ?
          AND d.stall_id = ?
        GROUP BY d.id;
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
    Body: { "weight": 60.0 }
    Disposer-only; updates weight (does NOT auto-complete).
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

    cur.execute("SELECT id, stall_id FROM demands WHERE id = ?;", (demand_id,))
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

    cur.execute("UPDATE demands SET weight = ? WHERE id = ?;", (weight, demand_id))
    conn.commit()

    cur.execute(
        """
        SELECT
            d.id,
            d.weight,
            d.status,
            d.stall_id,
            d.product_id,
            p.name           AS product_name,
            p.variant        AS product_variant,
            p.current_price  AS current_price,
            s.stall_name     AS stall_name,
            s.stall_location AS stall_location,
            COALESCE(COUNT(r.id), 0) AS requests_count
        FROM demands d
        JOIN products p ON d.product_id = p.id
        JOIN stalls   s ON d.stall_id = s.id
        LEFT JOIN requests r ON r.demand_id = d.id
        WHERE d.id = ?
        GROUP BY d.id;
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
    Disposer-only.
    (Still allowed, but use /complete for normal lifecycle)
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

    cur.execute("SELECT id, stall_id FROM demands WHERE id = ?;", (demand_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "demand not found"}), 404
    if row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    cur.execute("DELETE FROM demands WHERE id = ?;", (demand_id,))
    conn.commit()
    conn.close()

    return ("", 204)


@demand_bp.post("/<int:demand_id>/complete")
def complete_demand(demand_id):
    """
    POST /demands/<id>/complete
    Disposer-only.

    - Set demand.status='completed'
    - Set all related requests to status='completed'
    - DOES NOT delete the demand (keeps history)
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

    cur.execute("SELECT id, stall_id FROM demands WHERE id = ?;", (demand_id,))
    row = cur.fetchone()
    if not row or row["stall_id"] != stall_id:
        conn.close()
        return jsonify({"error": "demand not found"}), 404

    cur.execute("UPDATE demands SET status = 'completed' WHERE id = ?;", (demand_id,))
    cur.execute("UPDATE requests SET status = 'completed' WHERE demand_id = ?;", (demand_id,))

    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 200

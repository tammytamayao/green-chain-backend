# routes/supplies.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

supplies_bp = Blueprint("supplies", __name__, url_prefix="/supplies")


# ---------- Helpers ----------

def _require_farmer(request):
    """
    Returns ((user_row, conn), None) if authenticated farmer,
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

    if row["type"] != "farmer":
        conn.close()
        return None, (jsonify({"error": "forbidden, farmer only"}), 403)

    return (row, conn), None


def _supply_row_to_dict(row):
    return {
        "id": row["id"],
        "weight": row["weight"],
        "farmer_id": row["farmer_id"],
        "product_id": row["product_id"],
    }


def _request_row_to_dict(row):
    return {
        "id": row["id"],
        "price": row["price"],
        "method": row["method"],
        "status": row["status"],     # <- NEW
        "supply_id": row["supply_id"],
        "demand_id": row["demand_id"],
    }


# ---------- Routes ----------

@supplies_bp.post("")
def create_supply_and_request():
    """
    POST /supplies
    Body:
    {
      "product_id": 1,
      "weight": 25.0,
      "demand_id": 10,
      "price": 1300.0,
      "method": "gcash"   # or "cash"
    }

    Behavior:
    - Auth as farmer
    - Validate product, demand
    - Create a row in supplies (for this farmer + product)
    - Create a linked row in requests (for that supply + demand)
      with status='processing'.

    Returns:
    {
      "supply": { ... },
      "request": { ... }
    }
    """
    ctx, error_resp = _require_farmer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    weight = data.get("weight")
    demand_id = data.get("demand_id")
    price = data.get("price")
    method = (data.get("method") or "").strip().lower()

    # ---- Basic required fields ----
    if not product_id or weight is None or not demand_id or price is None or not method:
        conn.close()
        return jsonify(
            {
                "error": (
                    "product_id, weight, demand_id, price, method "
                    "are all required"
                )
            }
        ), 400

    # ---- Validate numeric fields ----
    try:
        weight = float(weight)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "weight must be a number"}), 400

    if weight <= 0:
        conn.close()
        return jsonify({"error": "weight must be > 0"}), 400

    try:
        price = float(price)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "price must be a number"}), 400

    if price < 0:
        conn.close()
        return jsonify({"error": "price must be >= 0"}), 400

    # Optional: limit method to known values
    if method not in ("gcash", "cash"):
        conn.close()
        return jsonify({"error": "method must be 'gcash' or 'cash'"}), 400

    # ---- Ensure product exists ----
    cur.execute(
        "SELECT id FROM products WHERE id = ?;",
        (product_id,),
    )
    product_row = cur.fetchone()
    if not product_row:
        conn.close()
        return jsonify({"error": "product not found"}), 404

    # ---- Ensure demand exists and matches product ----
    cur.execute(
        """
        SELECT id, weight, stall_id, product_id
        FROM demands
        WHERE id = ?;
        """,
        (demand_id,),
    )
    demand_row = cur.fetchone()
    if not demand_row:
        conn.close()
        return jsonify({"error": "demand not found"}), 404

    if demand_row["product_id"] != product_id:
        conn.close()
        return jsonify(
            {"error": "demand.product_id does not match product_id"}
        ), 400

    # Optional: ensure supplied weight <= demanded weight
    if weight > demand_row["weight"]:
        conn.close()
        return jsonify(
            {"error": "supplied weight cannot exceed demanded weight"}
        ), 400

    # ---- Insert into supplies ----
    farmer_id = user_row["id"]
    cur.execute(
        """
        INSERT INTO supplies (weight, farmer_id, product_id)
        VALUES (?, ?, ?);
        """,
        (weight, farmer_id, product_id),
    )
    supply_id = cur.lastrowid

    # ---- Insert into requests ----
    # status defaults to 'processing', but we set it explicitly for clarity
    cur.execute(
        """
        INSERT INTO requests (price, method, status, supply_id, demand_id)
        VALUES (?, ?, 'processing', ?, ?);
        """,
        (price, method, supply_id, demand_id),
    )
    request_id = cur.lastrowid

    conn.commit()

    # ---- Fetch created rows for response ----
    cur.execute(
        """
        SELECT id, weight, farmer_id, product_id
        FROM supplies
        WHERE id = ?;
        """,
        (supply_id,),
    )
    supply_row = cur.fetchone()

    cur.execute(
        """
        SELECT id, price, method, status, supply_id, demand_id
        FROM requests
        WHERE id = ?;
        """,
        (request_id,),
    )
    request_row = cur.fetchone()

    conn.close()

    return (
        jsonify(
            {
                "supply": _supply_row_to_dict(supply_row),
                "request": _request_row_to_dict(request_row),
            }
        ),
        201,
    )

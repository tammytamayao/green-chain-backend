# routes/requests.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

requests_bp = Blueprint("requests", __name__, url_prefix="/requests")

ALLOWED_METHODS = ("gcash", "cash")
ALLOWED_STATUS = ("processing", "accepted", "rejected", "completed")


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


def _require_farmer(req):
    ctx, error_resp = _require_user(req)
    if error_resp:
        return None, error_resp
    (user_row, conn) = ctx
    if user_row["type"] != "farmer":
        conn.close()
        return None, (jsonify({"error": "forbidden, farmer only"}), 403)
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


# ---------- Row mappers ----------

def request_with_context_row_to_dict(row):
    """
    Map a joined row (request + farm + stall) to a nested JSON-friendly dict.
    """
    return {
        "id": row["id"],
        "price": row["price"],
        "method": row["method"],
        "status": row["status"],
        "supply_id": row["supply_id"],
        "demand_id": row["demand_id"],
        "farm": {
            "farmer_id": row["farmer_id"],
            "farmer_username": row["farmer_username"],
            "farmer_first_name": row["farmer_first_name"],
            "farmer_last_name": row["farmer_last_name"],
            "farm_name": row["farm_name"],
            "farm_location": row["farm_location"],
        },
        "stall": {
            "stall_id": row["stall_id"],
            "stall_name": row["stall_name"],
            "stall_location": row["stall_location"],
            "stall_representative": row["stall_representative"],
        },
    }


# A reusable SELECT that joins everything needed
_REQUEST_BASE_SELECT = """
    SELECT
        r.id,
        r.price,
        r.method,
        r.status,
        r.supply_id,
        r.demand_id,

        -- farm / farmer
        s.farmer_id                         AS farmer_id,
        uf.username                         AS farmer_username,
        uf.first_name                       AS farmer_first_name,
        uf.last_name                        AS farmer_last_name,
        uf.farm_name                        AS farm_name,
        uf.farm_location                    AS farm_location,

        -- stall
        d.stall_id                          AS stall_id,
        st.stall_name                       AS stall_name,
        st.stall_location                   AS stall_location,
        st.representative                   AS stall_representative

    FROM requests r
    JOIN supplies s   ON r.supply_id = s.id
    JOIN users   uf   ON s.farmer_id = uf.id
    JOIN demands d    ON r.demand_id = d.id
    JOIN stalls  st   ON d.stall_id = st.id
"""


# ---------- Low-level creation helper (used by other modules) ----------

def create_request_record(cur, *, price, method, supply_id, demand_id):
    """
    Low-level helper to insert a request row and return it WITH farm + stall data.
    Expects inputs already validated. DOES NOT commit.
    """
    cur.execute(
        """
        INSERT INTO requests (price, method, status, supply_id, demand_id)
        VALUES (?, ?, 'processing', ?, ?);
        """,
        (price, method, supply_id, demand_id),
    )
    request_id = cur.lastrowid

    cur.execute(
        _REQUEST_BASE_SELECT + """
        WHERE r.id = ?;
        """,
        (request_id,),
    )
    row = cur.fetchone()
    return request_with_context_row_to_dict(row)


# ---------- Routes ----------

@requests_bp.post("")
def create_request():
    """
    POST /requests
    Body:
    {
      "supply_id": 1,
      "demand_id": 10,
      "price": 1300.0,
      "method": "gcash"   # or "cash"
    }

    Behavior:
    - Farmer-only.
    - supply_id must belong to current farmer.
    - demand.product_id must match supply.product_id.
    - Returns farm + stall info.
    """
    ctx, error_resp = _require_farmer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    supply_id = data.get("supply_id")
    demand_id = data.get("demand_id")
    price = data.get("price")
    method = (data.get("method") or "").strip().lower()

    if not supply_id or not demand_id or price is None or not method:
        conn.close()
        return jsonify(
            {"error": "supply_id, demand_id, price, method are all required"}
        ), 400

    try:
        price = float(price)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "price must be a number"}), 400

    if price < 0:
        conn.close()
        return jsonify({"error": "price must be >= 0"}), 400

    if method not in ALLOWED_METHODS:
        conn.close()
        return jsonify({"error": "method must be 'gcash' or 'cash'"}), 400

    # Ensure supply belongs to current farmer
    cur.execute(
        """
        SELECT id, farmer_id, product_id, weight
        FROM supplies
        WHERE id = ?;
        """,
        (supply_id,),
    )
    supply_row = cur.fetchone()
    if not supply_row:
        conn.close()
        return jsonify({"error": "supply not found"}), 404
    if supply_row["farmer_id"] != user_row["id"]:
        conn.close()
        return jsonify({"error": "forbidden, not your supply"}), 403

    # Ensure demand exists and matches product
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
    if demand_row["product_id"] != supply_row["product_id"]:
        conn.close()
        return jsonify(
            {"error": "demand.product_id does not match supply.product_id"}
        ), 400

    # Optional: ensure supply weight <= demand weight
    if supply_row["weight"] > demand_row["weight"]:
        conn.close()
        return jsonify(
            {"error": "supplied weight cannot exceed demanded weight"}
        ), 400

    request_dict = create_request_record(
        cur,
        price=price,
        method=method,
        supply_id=supply_id,
        demand_id=demand_id,
    )
    conn.commit()
    conn.close()

    return jsonify(request_dict), 201


@requests_bp.get("")
def list_requests():
    """
    GET /requests

    - For farmers: list requests linked to their supplies (with farm + stall).
    - For disposers: list requests linked to demands in their stall (with farm + stall).
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "farmer":
        # Requests where supply belongs to this farmer
        cur.execute(
            _REQUEST_BASE_SELECT + """
            JOIN supplies s2 ON r.supply_id = s2.id
            WHERE s2.farmer_id = ?
            ORDER BY r.id DESC;
            """,
            (user_row["id"],),
        )
    elif user_type == "disposer":
        # Requests where demand belongs to this disposer’s stall
        stall_id = _get_disposer_stall_id(cur, user_row["id"])
        if stall_id is None:
            conn.close()
            return jsonify([]), 200

        cur.execute(
            _REQUEST_BASE_SELECT + """
            JOIN demands d2 ON r.demand_id = d2.id
            WHERE d2.stall_id = ?
            ORDER BY r.id DESC;
            """,
            (stall_id,),
        )
    else:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    rows = cur.fetchall()
    conn.close()

    return jsonify([request_with_context_row_to_dict(r) for r in rows]), 200


@requests_bp.get("/<int:request_id>")
def get_request(request_id):
    """
    GET /requests/<id>

    - Farmer can read if request is linked to their supply.
    - Disposer can read if request is linked to their stall’s demand.
    - Includes farm + stall.
    """
    ctx, error_resp = _require_user(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    user_type = user_row["type"]

    if user_type == "farmer":
        cur.execute(
            _REQUEST_BASE_SELECT + """
            JOIN supplies s2 ON r.supply_id = s2.id
            WHERE r.id = ?
              AND s2.farmer_id = ?;
            """,
            (request_id, user_row["id"]),
        )
    elif user_type == "disposer":
        stall_id = _get_disposer_stall_id(cur, user_row["id"])
        if stall_id is None:
            conn.close()
            return jsonify({"error": "no stall found for disposer"}), 400

        cur.execute(
            _REQUEST_BASE_SELECT + """
            JOIN demands d2 ON r.demand_id = d2.id
            WHERE r.id = ?
              AND d2.stall_id = ?;
            """,
            (request_id, stall_id),
        )
    else:
        conn.close()
        return jsonify({"error": "forbidden"}), 403

    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "request not found"}), 404

    return jsonify(request_with_context_row_to_dict(row)), 200


@requests_bp.patch("/<int:request_id>")
def update_request_status(request_id):
    """
    PATCH /requests/<id>
    Body:
    {
      "status": "accepted" | "rejected" | "completed" | "processing"
    }

    Disposer-only: can change status of requests belonging to their stall.
    Returns updated request with farm + stall.
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

    # Ensure request belongs to this stall (via demand)
    cur.execute(
        """
        SELECT r.id
        FROM requests r
        JOIN demands d ON r.demand_id = d.id
        WHERE r.id = ?
          AND d.stall_id = ?;
        """,
        (request_id, stall_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "request not found"}), 404

    cur.execute(
        """
        UPDATE requests
        SET status = ?
        WHERE id = ?;
        """,
        (status, request_id),
    )

    cur.execute(
        _REQUEST_BASE_SELECT + """
        WHERE r.id = ?;
        """,
        (request_id,),
    )
    updated = cur.fetchone()
    conn.commit()
    conn.close()

    return jsonify(request_with_context_row_to_dict(updated)), 200


@requests_bp.delete("/<int:request_id>")
def delete_request(request_id):
    """
    DELETE /requests/<id>

    Farmer-only:
      - Can delete their own request while it's still 'processing'.
    """
    ctx, error_resp = _require_farmer(request)
    if error_resp:
        return error_resp
    (user_row, conn) = ctx
    cur = conn.cursor()

    cur.execute(
        """
        SELECT r.id, r.status, s.farmer_id
        FROM requests r
        JOIN supplies s ON r.supply_id = s.id
        WHERE r.id = ?;
        """,
        (request_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "request not found"}), 404

    if row["farmer_id"] != user_row["id"]:
        conn.close()
        return jsonify({"error": "forbidden, not your request"}), 403

    if row["status"] != "processing":
        conn.close()
        return jsonify(
            {"error": "only 'processing' requests can be deleted"}
        ), 400

    cur.execute(
        "DELETE FROM requests WHERE id = ?;",
        (request_id,),
    )
    conn.commit()
    conn.close()

    return ("", 204)

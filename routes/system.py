# routes/system.py
from flask import Blueprint, jsonify
from db import get_db

# No url_prefix here; we’ll define full paths on each route.
system_bp = Blueprint("system", __name__)


@system_bp.get("/health")
def health():
    return jsonify({"ok": True})


@system_bp.get("/system/metrics")
def system_metrics():
    """
    Returns high-level counts for admin monitoring:
      - users by type (farmer, disposer, driver, consumer)
      - total requests
      - total stalls
      - total orders
      - total feedbacks
    """
    conn = get_db()
    cur = conn.cursor()

    # Default counts (so missing types still show as 0)
    user_counts = {
        "farmer": 0,
        "disposer": 0,
        "driver": 0,
        "consumer": 0,
    }

    # Count users grouped by type, excluding admins
    cur.execute(
        """
        SELECT type, COUNT(*) AS cnt
        FROM users
        WHERE type IN ('farmer','disposer','driver','consumer')
        GROUP BY type;
        """
    )
    for row in cur.fetchall():
        user_counts[row["type"]] = row["cnt"]

    # Requests
    cur.execute("SELECT COUNT(*) AS c FROM requests;")
    requests_count = cur.fetchone()["c"]

    # Stalls
    cur.execute("SELECT COUNT(*) AS c FROM stalls;")
    stalls_count = cur.fetchone()["c"]

    # Orders
    cur.execute("SELECT COUNT(*) AS c FROM orders;")
    orders_count = cur.fetchone()["c"]

    # Feedbacks
    cur.execute("SELECT COUNT(*) AS c FROM feedbacks;")
    feedbacks_count = cur.fetchone()["c"]

    conn.close()

    return jsonify(
        {
            "users": user_counts,
            "requests": requests_count,
            "stalls": stalls_count,
            "orders": orders_count,
            "feedbacks": feedbacks_count,
        }
    )

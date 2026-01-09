# routes/user.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

user_bp = Blueprint("user", __name__)

@user_bp.get("/me")
def me():
    """
    Returns full profile info including role-specific fields and vehicles.
    """
    user_id, _ = auth_user(request)
    if not user_id:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor()

    # Fetch all scalar profile fields
    cur.execute(
        """
        SELECT
            id,
            username,
            first_name,
            last_name,
            contact_number,
            type,
            farm_name,
            farm_location,
            business,
            location,
            license_id
        FROM users
        WHERE id = ?;
        """,
        (user_id,),
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404

    user = dict(row)

    # Attach vehicles if driver
    if user["type"] == "driver":
        cur.execute(
            """
            SELECT
                model,
                class,
                plate_number
            FROM vehicles
            WHERE user_id = ?;
            """,
            (user_id,),
        )
        vehicles = [
            {
                "model": v["model"],
                "class": v["class"],
                "plate_number": v["plate_number"],
            }
            for v in cur.fetchall()
        ]
        user["vehicles"] = vehicles
    else:
        user["vehicles"] = []

    conn.close()
    return jsonify(user)

# routes/auth.py
from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import time

from db import get_db
from auth_utils import issue_token

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

@auth_bp.post("/register")
def register():
    """
    Body (general):
      first_name, last_name, contact_number, username, password, type
    Farmer  : + farm_name, farm_location
    Disposer: + business, location
    Driver  : + license_id, vehicles: [{model, class, plate_number}, ...]
    """
    data = request.get_json(silent=True) or {}

    # General
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    contact_number = (data.get("contact_number") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    utype = (data.get("type") or "").strip().lower()

    if not all([first_name, last_name, contact_number, username, password, utype]):
        return jsonify({"error": "all fields are required"}), 400
    if utype not in ("farmer", "disposer", "driver"):
        return jsonify({"error": "invalid type"}), 400

    # Type-specific
    farm_name = farm_location = business = location = license_id = None
    vehicles = []

    if utype == "farmer":
        farm_name = (data.get("farm_name") or "").strip()
        farm_location = (data.get("farm_location") or "").strip()
        if not farm_name or not farm_location:
            return jsonify(
                {"error": "farm_name and farm_location are required for farmer"}
            ), 400

    elif utype == "disposer":
        business = (data.get("business") or "").strip()
        location = (data.get("location") or "").strip()
        if not business or not location:
            return jsonify(
                {"error": "business and location are required for disposer"}
            ), 400

    elif utype == "driver":
        license_id = (data.get("license_id") or "").strip()
        if not license_id:
            return jsonify({"error": "license_id is required for driver"}), 400

        vehicles = data.get("vehicles") or []
        if not isinstance(vehicles, list) or len(vehicles) == 0:
            return jsonify(
                {"error": "vehicles must be a non-empty list for driver"}
            ), 400
        # basic validation of each vehicle
        for v in vehicles:
            if not all(
                isinstance(v.get(k, ""), str) and v.get(k, "").strip()
                for k in ("model", "class", "plate_number")
            ):
                return jsonify(
                    {"error": "vehicle requires model, class, plate_number"}
                ), 400

    # Insert user
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users
            (username, password_hash, first_name, last_name, contact_number, type,
             farm_name, farm_location, business, location, license_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                username,
                generate_password_hash(password),
                first_name,
                last_name,
                contact_number,
                utype,
                farm_name,
                farm_location,
                business,
                location,
                license_id,
                int(time.time()),
            ),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "username already taken"}), 409

    # If driver, insert vehicles
    if utype == "driver":
        for v in vehicles:
            cur.execute(
                """
                INSERT INTO vehicles (user_id, model, class, plate_number)
                VALUES (?, ?, ?, ?);
                """,
                (user_id, v["model"].strip(), v["class"].strip(), v["plate_number"].strip()),
            )
        conn.commit()

    # If disposer, automatically create stall
    if utype == "disposer":
        representative = f"{first_name} {last_name}"
        cur.execute(
            """
            INSERT INTO stalls (stall_name, stall_location, representative, user_id)
            VALUES (?, ?, ?, ?);
            """,
            (business, location, representative, user_id),
        )
        conn.commit()

    conn.close()

    token = issue_token(user_id, username)
    return jsonify(
        {
            "token": token,
            "user": {
                "id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "contact_number": contact_number,
                "type": utype,
                "farm_name": farm_name,
                "farm_location": farm_location,
                "business": business,
                "location": location,
                "license_id": license_id,
            },
        }
    ), 201

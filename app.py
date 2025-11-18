# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import os, sqlite3, time, jwt

# ===== Config =====
SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
DB_PATH = os.environ.get("DB_PATH", "app.db")

# ===== DB helpers =====
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Users table (includes all role-specific optional columns)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('farmer','disposer','driver')),

            -- farmer
            farm_name TEXT,
            farm_location TEXT,

            -- disposer
            entity TEXT,
            business TEXT,

            -- driver
            license_id TEXT,

            created_at INTEGER NOT NULL
        );
        """
    )

    # Vehicles (only for drivers, owned by a user)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            class TEXT NOT NULL,
            plate_number TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    # Todos (per user)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    conn.commit()
    conn.close()

# ===== App factory =====
def create_app():
    app = Flask(__name__)
    CORS(app)
    init_db()

    # ---- auth utils ----
    def issue_token(user_id, username):
        payload = {
            "sub": str(user_id),   # PyJWT requires a string 'sub'
            "username": username,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60 * 60 * 24 * 7,  # 7 days
        }
        return jwt.encode(payload, SECRET, algorithm="HS256")

    def auth_user(req):
        """Return (user_id, username) from Authorization: Bearer <token> or (None, None)."""
        auth = req.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return (None, None)
        token = auth.split(" ", 1)[1].strip()
        try:
            data = jwt.decode(token, SECRET, algorithms=["HS256"])
            return (int(data["sub"]), data.get("username"))
        except Exception:
            return (None, None)

    # ---- system ----
    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # ---- auth ----
    @app.post("/auth/register")
    def register():
        """
        Body (general):
          first_name, last_name, contact_number, username, password, type
        Farmer  : + farm_name, farm_location
        Disposer: + entity, business
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
        farm_name = farm_location = entity = business = license_id = None
        vehicles = []

        if utype == "farmer":
            farm_name = (data.get("farm_name") or "").strip()
            farm_location = (data.get("farm_location") or "").strip()
            if not farm_name or not farm_location:
                return jsonify(
                    {"error": "farm_name and farm_location are required for farmer"}
                ), 400

        elif utype == "disposer":
            entity = (data.get("entity") or "").strip()
            business = (data.get("business") or "").strip()
            if not entity or not business:
                return jsonify(
                    {"error": "entity and business are required for disposer"}
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
                 farm_name, farm_location, entity, business, license_id, created_at)
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
                    entity,
                    business,
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
                    "entity": entity,
                    "business": business,
                    "license_id": license_id,
                },
            }
        ), 201

    @app.post("/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?;",
            (username,),
        )
        row = cur.fetchone()
        conn.close()

        if not row or not check_password_hash(row["password_hash"], password):
            return jsonify({"error": "invalid credentials"}), 401

        token = issue_token(row["id"], row["username"])
        return jsonify({"token": token, "user": {"id": row["id"], "username": row["username"]}})

    @app.get("/me")
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
                entity,
                business,
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

    # ---- protected todos ----
    @app.get("/todos")
    def list_todos():
        user_id, _ = auth_user(request)
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, title, done FROM todos WHERE user_id = ? ORDER BY id;", (user_id,))
        todos = [dict(id=r[0], title=r[1], done=bool(r[2])) for r in cur.fetchall()]
        conn.close()
        return jsonify(todos)

    @app.post("/todos")
    def create_todo():
        user_id, _ = auth_user(request)
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO todos (user_id, title, done) VALUES (?, ?, 0);", (user_id, title))
        conn.commit()
        todo_id = cur.lastrowid
        conn.close()
        return jsonify({"id": todo_id, "title": title, "done": False}), 201

    @app.patch("/todos/<int:todo_id>")
    def toggle_todo(todo_id):
        user_id, _ = auth_user(request)
        if not user_id:
            return jsonify({"error": "unauthorized"}), 401
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE todos
               SET done = CASE done WHEN 1 THEN 0 ELSE 1 END
             WHERE id = ? AND user_id = ?;
            """,
            (todo_id, user_id),
        )
        conn.commit()
        cur.execute(
            "SELECT id, title, done FROM todos WHERE id = ? AND user_id = ?;",
            (todo_id, user_id),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"id": row[0], "title": row[1], "done": bool(row[2])})

    return app

if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5001, debug=True)

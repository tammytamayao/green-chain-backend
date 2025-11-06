from flask import Flask, jsonify, request
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app)

    TODOS = [
        {"id": 1, "title": "Hello from Flask (Poetry)", "done": False},
        {"id": 2, "title": "Tap in Flutter to toggle", "done": True},
    ]

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/todos")
    def list_todos():
        return jsonify(TODOS)

    @app.post("/todos")
    def create_todo():
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        new_id = (max(t["id"] for t in TODOS) + 1) if TODOS else 1
        todo = {"id": new_id, "title": title, "done": False}
        TODOS.append(todo)
        return jsonify(todo), 201

    @app.patch("/todos/<int:todo_id>")
    def toggle(todo_id):
        for t in TODOS:
            if t["id"] == todo_id:
                t["done"] = not t["done"]
                return jsonify(t)
        return jsonify({"error": "not found"}), 404

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)

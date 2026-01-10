# app.py
from flask import Flask
from flask_cors import CORS

from db import init_db
from routes.system import system_bp
from routes.auth import auth_bp
from routes.user import user_bp
from routes.products import product_bp  # <-- add this import


def create_app():
    app = Flask(__name__)
    CORS(app)

    # Initialize DB (creates tables if they don't exist)
    init_db()

    # Register blueprints
    app.register_blueprint(system_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(product_bp)  # <-- register products routes

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)

# routes/system.py
from flask import Blueprint, jsonify

system_bp = Blueprint("system", __name__)

@system_bp.get("/health")
def health():
    return jsonify({"ok": True})

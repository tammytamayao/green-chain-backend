# auth_utils.py
import time
import jwt
from flask import Request
from config import SECRET

def issue_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),   # PyJWT requires a string 'sub'
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60 * 60 * 24 * 7,  # 7 days
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")

def auth_user(req: Request):
    """
    Return (user_id, username) from Authorization: Bearer <token>
    or (None, None) if unauthorized/invalid.
    """
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return (None, None)
    token = auth.split(" ", 1)[1].strip()
    try:
        data = jwt.decode(token, SECRET, algorithms=["HS256"])
        return (int(data["sub"]), data.get("username"))
    except Exception:
        return (None, None)

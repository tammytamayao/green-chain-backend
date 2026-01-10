# routes/products.py
from flask import Blueprint, jsonify, request
from db import get_db
from auth_utils import auth_user

product_bp = Blueprint("product", __name__, url_prefix="/products")


def _require_admin(request):
    """
    Returns ( (user_row, conn), None ) if authenticated admin,
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

    if row["type"] != "admin":
        conn.close()
        return None, (jsonify({"error": "forbidden, admin only"}), 403)

    # return both row + open connection
    return (row, conn), None


@product_bp.get("")
def list_products():
    """
    Returns list of all products with current_price.

    [
      {
        "id": 1,
        "name": "Green Ice Lettuce",
        "variant": "Default",
        "current_price": 50.0
      },
      ...
    ]
    """
    user_id, _ = auth_user(request)
    if not user_id:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, variant, current_price
        FROM products
        ORDER BY name, variant;
        """
    )
    rows = cur.fetchall()
    conn.close()

    products = [
        {
            "id": r["id"],
            "name": r["name"],
            "variant": r["variant"],
            "current_price": r["current_price"],
        }
        for r in rows
    ]
    return jsonify(products), 200


@product_bp.post("")
def create_product():
    """
    POST /products
    Body:
      {
        "name": "Green Ice Lettuce",
        "variant": "Default",
        "current_price": 50.0   # optional
      }

    Admin-only.
    """
    ctx, error_resp = _require_admin(request)
    if error_resp:
        return error_resp
    (admin_user, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    variant = (data.get("variant") or "").strip()
    price = data.get("current_price", None)

    if not name or not variant:
        conn.close()
        return jsonify({"error": "name and variant are required"}), 400

    if price is not None:
        try:
            price = float(price)
        except (ValueError, TypeError):
            conn.close()
            return jsonify({"error": "current_price must be a number"}), 400
        if price < 0:
            conn.close()
            return jsonify({"error": "current_price must be >= 0"}), 400

    cur.execute(
        """
        INSERT INTO products (name, variant, current_price)
        VALUES (?, ?, ?);
        """,
        (name, variant, price),
    )
    conn.commit()
    product_id = cur.lastrowid

    cur.execute(
        """
        SELECT id, name, variant, current_price
        FROM products
        WHERE id = ?;
        """,
        (product_id,),
    )
    row = cur.fetchone()
    conn.close()

    return (
        jsonify(
            {
                "id": row["id"],
                "name": row["name"],
                "variant": row["variant"],
                "current_price": row["current_price"],
            }
        ),
        201,
    )


@product_bp.patch("/<int:product_id>")
def update_product_price(product_id):
    """
    PATCH /products/<id>
    Body: { "current_price": 123.45 }

    Admin-only.
    """
    ctx, error_resp = _require_admin(request)
    if error_resp:
        return error_resp
    (admin_user, conn) = ctx
    cur = conn.cursor()

    data = request.get_json(silent=True) or {}
    price = data.get("current_price", None)

    if price is None:
        conn.close()
        return jsonify({"error": "current_price is required"}), 400

    try:
        price = float(price)
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "current_price must be a number"}), 400

    if price < 0:
        conn.close()
        return jsonify({"error": "current_price must be >= 0"}), 400

    # Ensure product exists
    cur.execute(
        """
        SELECT id, name, variant, current_price
        FROM products
        WHERE id = ?;
        """,
        (product_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "product not found"}), 404

    # Update price
    cur.execute(
        """
        UPDATE products
        SET current_price = ?
        WHERE id = ?;
        """,
        (price, product_id),
    )
    conn.commit()

    # Fetch updated row
    cur.execute(
        """
        SELECT id, name, variant, current_price
        FROM products
        WHERE id = ?;
        """,
        (product_id,),
    )
    updated = cur.fetchone()
    conn.close()

    return (
        jsonify(
            {
                "id": updated["id"],
                "name": updated["name"],
                "variant": updated["variant"],
                "current_price": updated["current_price"],
            }
        ),
        200,
    )

@product_bp.delete("/<int:product_id>")
def delete_product(product_id):
    """
    DELETE /products/<id>

    Admin-only.
    """
    ctx, error_resp = _require_admin(request)
    if error_resp:
        return error_resp
    (admin_user, conn) = ctx
    cur = conn.cursor()

    # Ensure product exists
    cur.execute(
        """
        SELECT id
        FROM products
        WHERE id = ?;
        """,
        (product_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "product not found"}), 404

    # Delete product
    cur.execute(
        """
        DELETE FROM products
        WHERE id = ?;
        """,
        (product_id,),
    )
    conn.commit()
    conn.close()

    # 204 No Content = success with empty body
    return ("", 204)

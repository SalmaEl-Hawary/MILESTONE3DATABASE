import os
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from hashlib import scrypt
from secrets import token_bytes

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-in-production")


def db_config():
    host = os.getenv("MYSQL_HOST", "")
    if not host:
        raise RuntimeError("MYSQL_HOST is required. Configure the actual remote MySQL server in .env.")
    if host.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Localhost is intentionally rejected. Set MYSQL_HOST to the remote database host.")
    config = {
        "host": host,
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", ""),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "resale_analytics"),
        "autocommit": False,
    }
    if os.getenv("MYSQL_SSL", "true").lower() in {"1", "true", "yes", "on"}:
        config["ssl_disabled"] = False
        config["ssl_verify_cert"] = os.getenv("MYSQL_SSL_VERIFY_CERT", "false").lower() in {"1", "true", "yes", "on"}
        config["ssl_verify_identity"] = os.getenv("MYSQL_SSL_VERIFY_IDENTITY", "false").lower() in {"1", "true", "yes", "on"}
        if os.getenv("MYSQL_SSL_CA"):
            config["ssl_ca"] = os.getenv("MYSQL_SSL_CA")
    return config


def get_db():
    return mysql.connector.connect(**db_config())


def rows(cursor):
    result = cursor.fetchall()
    return [dict(zip(cursor.column_names, row)) for row in result]


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def json_rows(items):
    return [{key: json_value(value) for key, value in item.items()} for item in items]


def password_hash(password):
    salt = token_bytes(16)
    digest = scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def execute(sql, params=(), commit=False, fetch=True):
    connection = get_db()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        result = rows(cursor) if fetch else []
        if commit:
            connection.commit()
        return result
    finally:
        cursor.close()
        connection.close()


def api_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except mysql.connector.Error as exc:
            return jsonify({"error": f"Database error: {exc.msg}"}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
    return wrapped


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
@api_errors
def health():
    result = execute("SELECT 1 AS connected, DATABASE() AS database_name, @@hostname AS server_name")
    return jsonify({"ok": True, **json_rows(result)[0]})


@app.get("/api/options")
@api_errors
def options():
    return jsonify({
        "developments": json_rows(execute("SELECT development_id AS id, name, district, governorate FROM development ORDER BY name")),
        "property_types": json_rows(execute("SELECT DISTINCT prop_type_name AS name FROM property WHERE prop_type_name IS NOT NULL AND prop_type_name <> '' ORDER BY prop_type_name")),
        "areas": json_rows(execute("SELECT DISTINCT district AS name FROM property WHERE district IS NOT NULL AND district <> '' ORDER BY district")),
        "amenities": json_rows(execute("SELECT DISTINCT amenity_name AS name FROM property_amenity ORDER BY amenity_name")),
        "contract_years": json_rows(execute("SELECT DISTINCT YEAR(delivery_date) AS year FROM property WHERE delivery_date IS NOT NULL ORDER BY year DESC")),
    })


@app.post("/api/register")
@api_errors
def register():
    data = request.get_json(force=True)
    required = ["email", "fullname", "username", "birthdate", "gender", "phone_number", "password"]
    missing = [field for field in required if not str(data.get(field, "")).strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if data["gender"] not in {"male", "female"}:
        raise ValueError("Gender must be male or female.")
    if len(data["password"]) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    focus_areas = [x.strip() for x in data.get("focus_areas", []) if x.strip()]
    connection = get_db()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO app_user (email, fullname, username, birthdate, gender, created_at, phone_number, password_hash) VALUES (%s,%s,%s,%s,%s,NOW(),%s,%s)",
            (data["email"].strip().lower(), data["fullname"].strip(), data["username"].strip(), data["birthdate"], data["gender"], data["phone_number"].strip(), password_hash(data["password"])),
        )
        for area in focus_areas:
            cursor.execute("INSERT INTO user_focus_area (email, focus_area) VALUES (%s, %s)", (data["email"].strip().lower(), area))
        connection.commit()
        session["email"] = data["email"].strip().lower()
        return jsonify({"message": "Registration successful.", "email": session["email"]}), 201
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


@app.post("/api/reviews")
@api_errors
def add_review():
    data = request.get_json(force=True)
    email = data.get("user_email") or session.get("email")
    if not email or not data.get("development_id") or not data.get("rating"):
        raise ValueError("user_email, development_id, and rating are required.")
    rating = int(data["rating"])
    if rating < 1 or rating > 10:
        raise ValueError("Rating must be between 1 and 10.")
    next_id = execute("SELECT COALESCE(MAX(review_id), 0) + 1 AS next_id FROM review")[0]["next_id"]
    execute("INSERT INTO review (review_id, user_email, rating, review_text, created_at, development_id) VALUES (%s,%s,%s,%s,NOW(),%s)", (next_id, email, rating, data.get("review_text", "").strip(), int(data["development_id"])), commit=True, fetch=False)
    return jsonify({"message": "Review added successfully.", "review_id": next_id}), 201


@app.get("/api/reviews/<int:development_id>")
@api_errors
def reviews(development_id):
    sql = """
        SELECT r.review_id, r.rating, r.review_text, r.created_at, u.username, u.fullname
        FROM review r JOIN app_user u ON u.email = r.user_email
        WHERE r.development_id = %s ORDER BY r.created_at DESC
    """
    return jsonify({"reviews": json_rows(execute(sql, (development_id,)))})


@app.get("/api/analytics/top-projects")
@api_errors
def top_projects():
    prop_type = request.args.get("property_type", "").strip()
    where, params = ("WHERE p.prop_type_name = %s", [prop_type]) if prop_type else ("", [])
    sql = f"""
        SELECT d.name AS project, p.prop_type_name AS property_type,
          COUNT(*) AS resale_inventory,
          COALESCE(SUM(p.asking_price),0) AS resale_amount,
          COALESCE(SUM(p.current_market_value - (p.asking_price + COALESCE(p.original_price - p.amount_paid_to_date,0))),0) AS total_gain_over_market
        FROM property p JOIN development d ON d.development_id = p.development_id
        {where} GROUP BY d.development_id, d.name, p.prop_type_name
        ORDER BY resale_inventory DESC, resale_amount DESC LIMIT 10
    """
    return jsonify({"rows": json_rows(execute(sql, params))})


@app.get("/api/analytics/development-summary/<int:development_id>")
@api_errors
def development_summary(development_id):
    location = execute("SELECT d.name AS project, d.country, d.governorate, d.district, COUNT(p.property_id) AS listings FROM development d LEFT JOIN property p ON p.development_id=d.development_id WHERE d.development_id=%s GROUP BY d.development_id,d.name,d.country,d.governorate,d.district", (development_id,))
    by_type = execute("SELECT prop_type_name AS property_type, COUNT(*) AS listings, ROUND(AVG(asking_price / NULLIF(area,0)),2) AS avg_price_per_sqm FROM property WHERE development_id=%s GROUP BY prop_type_name ORDER BY listings DESC", (development_id,))
    return jsonify({"location": json_rows(location), "by_type": json_rows(by_type)})


@app.get("/api/analytics/area-properties")
@api_errors
def area_properties():
    area = request.args.get("area", "").strip()
    if not area:
        raise ValueError("Choose an area.")
    sql = """
        SELECT p.property_id, p.title, d.name AS project, p.prop_type_name AS property_type,
          p.area, p.asking_price, ROUND(p.asking_price / NULLIF(p.area,0),2) AS price_per_sqm,
          p.number_of_bedrooms, p.number_of_bathrooms, p.payment_method, p.finish_type
        FROM property p JOIN development d ON d.development_id=p.development_id
        WHERE p.district=%s ORDER BY p.asking_price
    """
    summary = execute("SELECT prop_type_name AS property_type, COUNT(*) AS listings, ROUND(AVG(asking_price / NULLIF(area,0)),2) AS avg_price_per_sqm FROM property WHERE district=%s GROUP BY prop_type_name ORDER BY listings DESC", (area,))
    return jsonify({"rows": json_rows(execute(sql, (area,))), "by_type": json_rows(summary)})


@app.get("/api/analytics/search")
@api_errors
def search_properties():
    area = request.args.get("area", "").strip()
    low = request.args.get("min_price", "").strip()
    high = request.args.get("max_price", "").strip()
    amenities = [a.strip() for a in request.args.getlist("amenity") if a.strip()]
    if not area:
        raise ValueError("Choose an area.")
    filters = ["p.district=%s"]
    params = [area]
    if low:
        filters.append("p.asking_price >= %s"); params.append(float(low))
    if high:
        filters.append("p.asking_price <= %s"); params.append(float(high))
    amenity_sql = ""
    if amenities:
        placeholders = ",".join(["%s"] * len(amenities))
        amenity_sql = f"AND p.property_id IN (SELECT property_id FROM property_amenity WHERE amenity_name IN ({placeholders}) GROUP BY property_id HAVING COUNT(DISTINCT amenity_name) = %s)"
        params.extend(amenities); params.append(len(amenities))
    sql = f"SELECT p.property_id, p.title, d.name AS project, p.prop_type_name AS property_type, p.area, p.asking_price, ROUND(p.asking_price / NULLIF(p.area,0),2) AS price_per_sqm, p.payment_method FROM property p JOIN development d ON d.development_id=p.development_id WHERE {' AND '.join(filters)} {amenity_sql} ORDER BY p.asking_price"
    return jsonify({"rows": json_rows(execute(sql, params))})


@app.get("/api/analytics/developers")
@api_errors
def developers():
    sql = """
        SELECT dev.name AS developer, COUNT(p.property_id) AS resale_listings,
          ROUND(AVG(p.asking_price / NULLIF(p.area,0)),2) AS avg_price_per_sqm,
          ROUND(AVG(p.down_payment),2) AS avg_down_payment,
          ROUND(AVG(p.current_market_value - (p.asking_price + COALESCE(p.original_price - p.amount_paid_to_date,0))),2) AS avg_gain_over_market
        FROM developer dev JOIN development d ON d.developer_id=dev.developer_id
        JOIN property p ON p.development_id=d.development_id
        GROUP BY dev.developer_id, dev.name ORDER BY resale_listings DESC LIMIT 5
    """
    return jsonify({"rows": json_rows(execute(sql))})


@app.get("/api/analytics/by-year")
@api_errors
def by_year():
    clauses, params = [], []
    for key, column in [("area", "p.district"), ("property_type", "p.prop_type_name"), ("year", "YEAR(p.delivery_date)")]:
        value = request.args.get(key, "").strip()
        if value:
            clauses.append(f"{column}=%s"); params.append(int(value) if key == "year" else value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT p.property_id, p.title, d.name AS project, p.district AS area, p.prop_type_name AS property_type, YEAR(p.delivery_date) AS contract_year, p.asking_price, p.area AS sqm FROM property p JOIN development d ON d.development_id=p.development_id {where} ORDER BY contract_year DESC, p.asking_price"
    return jsonify({"rows": json_rows(execute(sql, params))})


@app.get("/api/analytics/area-development")
@api_errors
def area_development():
    sql = """
      SELECT p.district AS area, d.name AS project,
        ROUND(AVG(p.installment_amount),2) AS avg_installment_price,
        ROUND(AVG(p.down_payment),2) AS avg_down_payment,
        COUNT(*) AS number_of_properties,
        ROUND(AVG(p.current_market_value - p.asking_price),2) AS avg_discount_over_market_price,
        ROUND(AVG(YEAR(p.delivery_date)), 2) AS avg_contract_year,
        ROUND(AVG(p.current_market_value),2) AS avg_market_price
      FROM property p JOIN development d ON d.development_id=p.development_id
      GROUP BY p.district, d.development_id, d.name ORDER BY p.district, d.name
    """
    return jsonify({"rows": json_rows(execute(sql))})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)

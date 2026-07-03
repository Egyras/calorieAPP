#!/usr/bin/env python3
"""Calorie Tracker — Flask web app with Google OAuth and macro tracking."""
import os, sys, json, sqlite3, functools
from datetime import datetime, date, timedelta
from flask import Flask, render_template_string, request, g, jsonify, redirect, url_for, session, flash
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-abc123")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Google OAuth config
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ALLOWED_EMAILS       = [e.strip() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
DB_PATH              = os.environ.get("DB_PATH", "/data/calories.db")

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT UNIQUE NOT NULL,
                name       TEXT,
                picture    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS products (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                kcal       REAL NOT NULL DEFAULT 0,
                fat        REAL NOT NULL DEFAULT 0,
                protein    REAL NOT NULL DEFAULT 0,
                carbs      REAL NOT NULL DEFAULT 0,
                per_grams  REAL NOT NULL DEFAULT 100,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS daily_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                grams      REAL NOT NULL,
                log_date   TEXT NOT NULL,
                meal       TEXT DEFAULT 'other',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            );
            CREATE TABLE IF NOT EXISTS daily_goals (
                user_id    INTEGER PRIMARY KEY,
                kcal       REAL DEFAULT 2000,
                fat        REAL DEFAULT 65,
                protein    REAL DEFAULT 50,
                carbs      REAL DEFAULT 300,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)
        db.commit()
        g.db = db
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()


def ensure_default_products(db, user_id):
    """Add default Lithuanian food products if missing."""
    existing_names = set(r[0] for r in db.execute("SELECT name FROM products WHERE user_id=?", (user_id,)).fetchall())
    defaults = [
        ("Pomidorai", 18, 0.2, 0.9, 3.9, 100),
        ("Agurkai", 15, 0.1, 0.7, 3.6, 100),
        ("Bulves (virtos)", 77, 0.1, 2.0, 17.0, 100),
        ("Vistienos krutinele", 165, 3.6, 31.0, 0.0, 100),
        ("Kiausienis (virtas)", 155, 11.0, 13.0, 1.1, 100),
        ("Varske 9%", 159, 9.0, 16.5, 3.0, 100),
        ("Grietine 20%", 204, 20.0, 2.8, 3.6, 100),
        ("Juoda duona", 216, 1.3, 6.8, 42.0, 100),
        ("Ryziai (virti)", 130, 0.3, 2.7, 28.0, 100),
        ("Grikiai (virti)", 92, 0.6, 3.4, 19.9, 100),
        ("Bananai", 89, 0.3, 1.1, 23.0, 100),
        ("Obuoliai", 52, 0.2, 0.3, 14.0, 100),
        ("Pienas 2.5%", 52, 2.5, 3.2, 4.7, 100),
        ("Lasiosos file", 208, 13.0, 20.0, 0.0, 100),
        ("Avizine kose", 68, 1.4, 2.4, 12.0, 100),
        ("Sviestas 82%", 717, 81.0, 0.9, 0.1, 100),
        ("Sviezias svogunas", 40, 0.1, 1.1, 9.3, 100),
        ("Morkos", 41, 0.2, 0.9, 10.0, 100),
        ("Kiaulienos sonine", 458, 45.0, 12.0, 0.0, 100),
        ("Jogurtas naturalus", 59, 1.5, 10.0, 3.6, 100),
    ]
    added = 0
    for name, kcal, fat, protein, carbs, per in defaults:
        if name not in existing_names:
            db.execute(
                "INSERT INTO products (user_id, name, kcal, fat, protein, carbs, per_grams) VALUES (?,?,?,?,?,?,?)",
                (user_id, name, kcal, fat, protein, carbs, per)
            )
            added += 1
    print(f"[DEFAULTS] user {user_id}: {len(existing_names)} existing, added {added} defaults", flush=True)

@app.route("/login")
def login():
    if not GOOGLE_CLIENT_ID:
        return render_template_string(LOGIN_NO_OAUTH)
    return render_template_string(LOGIN_PAGE, google_client_id=GOOGLE_CLIENT_ID)

@app.route("/auth/google", methods=["POST"])
def google_auth():
    """Handle Google Sign-In credential callback."""
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
        token = request.form.get("credential") or request.json.get("credential", "")
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo["email"]
        if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
            flash("Access denied. Your email is not authorized.")
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if user:
            session["user_id"] = user["id"]
            db.execute("UPDATE users SET name=?, picture=? WHERE id=?",
                       (idinfo.get("name"), idinfo.get("picture"), user["id"]))
            ensure_default_products(db, user["id"])
        else:
            cur = db.execute("INSERT INTO users (email, name, picture) VALUES (?,?,?)",
                             (email, idinfo.get("name"), idinfo.get("picture")))
            session["user_id"] = cur.lastrowid
            db.execute("INSERT INTO daily_goals (user_id) VALUES (?)", (cur.lastrowid,))
            ensure_default_products(db, cur.lastrowid)
        db.commit()
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("index"))
    except Exception as e:
        print(f"[auth] {e}", file=sys.stderr)
        if request.is_json:
            return jsonify({"ok": False, "error": str(e)}), 401
        flash(f"Authentication failed: {e}")
        return redirect(url_for("login"))

@app.route("/auth/dev", methods=["POST"])
def dev_auth():
    """Simple email login (works in any browser)."""
    email = request.form.get("email", "dev@localhost")
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        flash("Access denied. Your email is not authorized.")
        return redirect(url_for("login"))
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if user:
        session["user_id"] = user["id"]
        ensure_default_products(db, user["id"])
    else:
        cur = db.execute("INSERT INTO users (email, name) VALUES (?,?)", (email, email.split("@")[0]))
        session["user_id"] = cur.lastrowid
        db.execute("INSERT INTO daily_goals (user_id) VALUES (?)", (cur.lastrowid,))
        ensure_default_products(db, cur.lastrowid)
    db.commit()
    return redirect(url_for("index"))


@app.route("/favicon.ico")
def favicon():
    return "", 204
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    today = request.args.get("date", date.today().isoformat())
    db = get_db()
    uid = session["user_id"]
    user = current_user()
    goals = db.execute("SELECT * FROM daily_goals WHERE user_id=?", (uid,)).fetchone()
    products = db.execute("SELECT * FROM products WHERE user_id=? ORDER BY name", (uid,)).fetchall()
    top_products = db.execute("""
        SELECT p.*, COUNT(dl.id) as use_count FROM products p
        JOIN daily_log dl ON dl.product_id = p.id AND dl.user_id = p.user_id
        WHERE p.user_id=?
        GROUP BY p.id ORDER BY use_count DESC LIMIT 8
    """, (uid,)).fetchall()
    log_entries = db.execute("""
        SELECT dl.id, dl.grams, dl.meal, dl.log_date,
               p.name, p.kcal, p.fat, p.protein, p.carbs, p.per_grams
        FROM daily_log dl JOIN products p ON dl.product_id = p.id
        WHERE dl.user_id=? AND dl.log_date=?
        ORDER BY dl.created_at
    """, (uid, today)).fetchall()

    totals = {"kcal": 0, "fat": 0, "protein": 0, "carbs": 0}
    entries = []
    for e in log_entries:
        ratio = e["grams"] / e["per_grams"]
        entry_kcal = round(e["kcal"] * ratio, 1)
        entry_fat = round(e["fat"] * ratio, 1)
        entry_protein = round(e["protein"] * ratio, 1)
        entry_carbs = round(e["carbs"] * ratio, 1)
        totals["kcal"] += entry_kcal
        totals["fat"] += entry_fat
        totals["protein"] += entry_protein
        totals["carbs"] += entry_carbs
        entries.append({
            "id": e["id"], "name": e["name"], "grams": e["grams"],
            "meal": e["meal"],
            "kcal": entry_kcal, "fat": entry_fat,
            "protein": entry_protein, "carbs": entry_carbs
        })

    for k in totals:
        totals[k] = round(totals[k], 1)

    # Week history for chart
    week_data = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        row = db.execute("""
            SELECT COALESCE(SUM(p.kcal * dl.grams / p.per_grams), 0) kcal,
                   COALESCE(SUM(p.protein * dl.grams / p.per_grams), 0) protein,
                   COALESCE(SUM(p.fat * dl.grams / p.per_grams), 0) fat,
                   COALESCE(SUM(p.carbs * dl.grams / p.per_grams), 0) carbs
            FROM daily_log dl JOIN products p ON dl.product_id = p.id
            WHERE dl.user_id=? AND dl.log_date=?
        """, (uid, d)).fetchone()
        week_data.append({"date": d, "kcal": round(row["kcal"], 0),
                          "protein": round(row["protein"], 1),
                          "fat": round(row["fat"], 1),
                          "carbs": round(row["carbs"], 1)})

    return render_template_string(MAIN_PAGE,
        user=user, today=today, products=products, top_products=top_products,
        entries=entries, totals=totals, goals=goals,
        week_data=json.dumps(week_data))

@app.route("/api/jslog", methods=["POST"])
def js_log():
    """Receive browser JS logs and print to stdout (docker logs)."""
    import sys
    data = request.get_json(silent=True) or {}
    msg = data.get("msg", "")
    level = data.get("level", "INFO")
    print(f"[JS {level}] {msg}", flush=True)
    return "ok", 200


@app.route("/api/barcode/<code>")
@login_required
def barcode_lookup(code):
    """Look up product nutrition from OpenFoodFacts by barcode."""
    import requests as req_lib
    urls = [
        f"https://world.openfoodfacts.org/api/v2/product/{code}.json?fields=product_name,brands,nutriments",
        f"https://world.openfoodfacts.net/api/v2/product/{code}.json?fields=product_name,brands,nutriments",
    ]
    for url in urls:
        try:
            print(f"[BARCODE] Trying: {url}", flush=True)
            resp = req_lib.get(url, headers={"User-Agent": "CalorieTracker/1.0 (ctfc54596@gmail.com)"}, timeout=10)
            print(f"[BARCODE] Status: {resp.status_code}", flush=True)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("status") != 1:
                return jsonify({"found": False}), 200
            p = data.get("product", {})
            n = p.get("nutriments", {})
            return jsonify({
                "found": True,
                "name": p.get("product_name", ""),
                "brand": p.get("brands", ""),
                "kcal": round(n.get("energy-kcal_100g", 0)),
                "fat": n.get("fat_100g", 0),
                "protein": n.get("proteins_100g", 0),
                "carbs": n.get("carbohydrates_100g", 0),
            })
        except Exception as e:
            print(f"[BARCODE] Error with {url}: {e}", flush=True)
            continue
    return jsonify({"found": False, "error": "Could not reach OpenFoodFacts"}), 200


@app.route("/api/products", methods=["POST"])
@login_required
def add_product():
    uid = session["user_id"]
    db = get_db()
    db.execute("INSERT INTO products (user_id, name, kcal, fat, protein, carbs, per_grams) VALUES (?,?,?,?,?,?,?)",
               (uid, request.form["name"],
                float(request.form.get("kcal", 0)),
                float(request.form.get("fat", 0)),
                float(request.form.get("protein", 0)),
                float(request.form.get("carbs", 0)),
                float(request.form.get("per_grams", 100))))
    db.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/api/products/<int:pid>", methods=["POST"])
@login_required
def update_product(pid):
    uid = session["user_id"]
    db = get_db()
    db.execute("""UPDATE products SET name=?, kcal=?, fat=?, protein=?, carbs=?, per_grams=?
                  WHERE id=? AND user_id=?""",
               (request.form["name"],
                float(request.form.get("kcal", 0)),
                float(request.form.get("fat", 0)),
                float(request.form.get("protein", 0)),
                float(request.form.get("carbs", 0)),
                float(request.form.get("per_grams", 100)),
                pid, uid))
    db.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/api/products/<int:pid>/delete", methods=["POST"])
@login_required
def delete_product(pid):
    uid = session["user_id"]
    db = get_db()
    db.execute("DELETE FROM daily_log WHERE product_id=? AND user_id=?", (pid, uid))
    db.execute("DELETE FROM products WHERE id=? AND user_id=?", (pid, uid))
    db.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/api/log", methods=["POST"])
@login_required
def add_log():
    uid = session["user_id"]
    db = get_db()
    if request.is_json:
        data = request.get_json()
        log_date = data.get("log_date", date.today().isoformat())
        db.execute("INSERT INTO daily_log (user_id, product_id, grams, log_date, meal) VALUES (?,?,?,?,?)",
                   (uid, int(data["product_id"]), float(data["grams"]), log_date, data.get("meal", "other")))
        db.commit()
        # Return updated totals
        row = db.execute("""
            SELECT COALESCE(SUM(p.kcal * dl.grams / p.per_grams),0) as kcal,
                   COALESCE(SUM(p.fat * dl.grams / p.per_grams),0) as fat,
                   COALESCE(SUM(p.protein * dl.grams / p.per_grams),0) as protein,
                   COALESCE(SUM(p.carbs * dl.grams / p.per_grams),0) as carbs
            FROM daily_log dl JOIN products p ON dl.product_id=p.id
            WHERE dl.user_id=? AND dl.log_date=?
        """, (uid, log_date)).fetchone()
        # Get product info for the entry
        prod = db.execute("SELECT name, kcal, fat, protein, carbs, per_grams FROM products WHERE id=?", (int(data["product_id"]),)).fetchone()
        g = float(data["grams"])
        ratio = g / prod["per_grams"]
        entry = {"name": prod["name"], "grams": g, "meal": data.get("meal","other"),
                 "kcal": round(prod["kcal"]*ratio,1), "fat": round(prod["fat"]*ratio,1),
                 "protein": round(prod["protein"]*ratio,1), "carbs": round(prod["carbs"]*ratio,1)}
        return jsonify({"ok": True, "entry": entry, "totals": {"kcal": round(row["kcal"],1), "fat": round(row["fat"],1), "protein": round(row["protein"],1), "carbs": round(row["carbs"],1)}})
    log_date = request.form.get("log_date", date.today().isoformat())
    db.execute("INSERT INTO daily_log (user_id, product_id, grams, log_date, meal) VALUES (?,?,?,?,?)",
               (uid, int(request.form["product_id"]),
                float(request.form["grams"]),
                log_date,
                request.form.get("meal", "other")))
    db.commit()
    return redirect(url_for("index", date=log_date))

@app.route("/api/log/<int:lid>/delete", methods=["POST"])
@login_required
def delete_log(lid):
    uid = session["user_id"]
    db = get_db()
    entry = db.execute("SELECT log_date FROM daily_log WHERE id=? AND user_id=?", (lid, uid)).fetchone()
    log_date = entry["log_date"] if entry else date.today().isoformat()
    db.execute("DELETE FROM daily_log WHERE id=? AND user_id=?", (lid, uid))
    db.commit()
    return redirect(url_for("index", date=log_date))

@app.route("/api/goals", methods=["POST"])
@login_required
def update_goals():
    uid = session["user_id"]
    db = get_db()
    db.execute("""INSERT INTO daily_goals (user_id, kcal, fat, protein, carbs) VALUES (?,?,?,?,?)
                  ON CONFLICT(user_id) DO UPDATE SET kcal=?, fat=?, protein=?, carbs=?""",
               (uid,
                float(request.form.get("kcal", 2000)),
                float(request.form.get("fat", 65)),
                float(request.form.get("protein", 50)),
                float(request.form.get("carbs", 300)),
                float(request.form.get("kcal", 2000)),
                float(request.form.get("fat", 65)),
                float(request.form.get("protein", 50)),
                float(request.form.get("carbs", 300))))
    db.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/products")
@login_required
def products_page():
    uid = session["user_id"]
    db = get_db()
    user = current_user()
    products = db.execute("SELECT * FROM products WHERE user_id=? ORDER BY name", (uid,)).fetchall()
    return render_template_string(PRODUCTS_PAGE, user=user, products=products)

@app.route("/history")
@login_required
def history_page():
    uid = session["user_id"]
    db = get_db()
    user = current_user()
    goals = db.execute("SELECT * FROM daily_goals WHERE user_id=?", (uid,)).fetchone()
    days = db.execute("""
        SELECT dl.log_date,
               ROUND(SUM(p.kcal * dl.grams / p.per_grams), 0) kcal,
               ROUND(SUM(p.fat * dl.grams / p.per_grams), 1) fat,
               ROUND(SUM(p.protein * dl.grams / p.per_grams), 1) protein,
               ROUND(SUM(p.carbs * dl.grams / p.per_grams), 1) carbs,
               COUNT(*) items
        FROM daily_log dl JOIN products p ON dl.product_id = p.id
        WHERE dl.user_id=?
        GROUP BY dl.log_date ORDER BY dl.log_date DESC LIMIT 30
    """, (uid,)).fetchall()
    return render_template_string(HISTORY_PAGE, user=user, days=days, goals=goals)

# ── Templates ─────────────────────────────────────────────────────────────────

STYLE = """
<style>
:root{
  --bg:#0a0d12;--bg-elev:#0f131a;--surface:#141821;--surface2:#1c2230;--surface3:#252b3a;
  --border:#252b36;--border-strong:#323a4a;
  --accent:#4ade80;--accent-bright:#6ee7a0;--accent-dim:#166534;--accent-glow:rgba(74,222,128,.35);
  --text:#d1d7e0;--text-strong:#f0f3f8;--muted:#8b95a8;--muted-soft:#5f6776;
  --green:#3fb950;--green-soft:#56d364;--amber:#d29922;--blue:#58a6ff;
  --red:#e5001a;--red-bright:#ff2640;
  --radius:12px;--radius-sm:8px;
  --ease:cubic-bezier(.4,0,.2,1);
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);--shadow:0 4px 12px rgba(0,0,0,.35);
}
*{box-sizing:border-box;margin:0;padding:0;}
body{
  font-family:'Inter','-apple-system','Segoe UI',system-ui,sans-serif;
  background:var(--bg);
  background-image:radial-gradient(ellipse 80% 50% at 50% -10%,rgba(74,222,128,.04),transparent 70%);
  background-attachment:fixed;
  color:var(--text);min-height:100vh;font-size:14px;line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--accent-bright);text-decoration:none;}

/* NAV */
.nav{
  background:rgba(10,13,18,.85);backdrop-filter:blur(20px) saturate(180%);
  border-bottom:1px solid rgba(255,255,255,.06);
  padding:0 1.5rem;display:flex;align-items:center;height:56px;
  position:sticky;top:0;z-index:1100;
}
.nav-brand{display:flex;align-items:center;gap:10px;text-decoration:none!important;}
.nav-brand-icon{
  width:30px;height:30px;border-radius:7px;
  background:linear-gradient(135deg,var(--accent) 0%,#166534 100%);
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
  box-shadow:0 2px 8px rgba(74,222,128,.4);font-size:16px;
}
.nav-brand-name{font-size:14px;font-weight:600;color:var(--text-strong);letter-spacing:-.015em;}
.nav-links{display:flex;gap:4px;margin-left:auto;align-items:center;}
.nav-link{
  display:flex;align-items:center;gap:6px;padding:7px 12px;
  border-radius:8px;font-size:13px;font-weight:500;
  color:var(--muted);text-decoration:none!important;border:1px solid transparent;
  transition:all .2s var(--ease);
}
.nav-link:hover{color:var(--text);background:rgba(255,255,255,.04);}
.nav-link.active{color:var(--text-strong);background:rgba(74,222,128,.12);border-color:rgba(74,222,128,.3);}
.nav-avatar{width:26px;height:26px;border-radius:50%;margin-left:8px;}

.container{max-width:900px;margin:0 auto;padding:1.5rem;}

/* CARDS */
.card{
  background:linear-gradient(180deg,var(--surface) 0%,var(--bg-elev) 100%);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:1.25rem;margin-bottom:1rem;box-shadow:var(--shadow-sm);
}
.card-title{
  font-size:11px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:.75rem;
  display:flex;align-items:center;gap:8px;
}
.card-title::before{content:'';display:inline-block;width:3px;height:14px;background:var(--accent);border-radius:2px;}

/* STATS */
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;margin-bottom:1.25rem;}
@media(max-width:600px){.stat-grid{grid-template-columns:repeat(2,1fr);}}
.stat-card{
  background:linear-gradient(180deg,var(--surface) 0%,var(--bg-elev) 100%);
  border:1px solid var(--border);border-radius:var(--radius);
  padding:1rem;position:relative;overflow:hidden;
}
.stat-num{font-size:26px;font-weight:600;line-height:1;letter-spacing:-.02em;}
.stat-lbl{font-size:11px;color:var(--muted);margin-top:6px;text-transform:uppercase;letter-spacing:.05em;font-weight:500;}
.stat-bar{height:4px;background:var(--surface2);border-radius:2px;margin-top:8px;overflow:hidden;}
.stat-fill{height:4px;border-radius:2px;transition:width .6s var(--ease);}
.kcal-color{color:#4ade80;} .kcal-fill{background:linear-gradient(90deg,#166534,#4ade80);}
.fat-color{color:#f59e0b;} .fat-fill{background:linear-gradient(90deg,#92400e,#f59e0b);}
.protein-color{color:#3b82f6;} .protein-fill{background:linear-gradient(90deg,#1e3a5f,#3b82f6);}
.carbs-color{color:#a78bfa;} .carbs-fill{background:linear-gradient(90deg,#4c1d95,#a78bfa);}

/* FORMS */
.form-row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:end;}
.form-group{display:flex;flex-direction:column;gap:4px;flex:1;min-width:80px;}
.form-group.wide{flex:2;min-width:150px;}
.form-group label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-weight:500;}
.form-group input,.form-group select{
  background:var(--surface2);border:1px solid var(--border);color:var(--text-strong);
  padding:9px 11px;border-radius:8px;font-size:13px;font-family:inherit;outline:none;
  transition:border-color .2s,box-shadow .2s;min-width:0;width:100%;box-sizing:border-box;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.log-form-row{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:.5rem;align-items:end;}
.log-form-row .form-group{min-width:0;}
@media(max-width:600px){
  .log-form-row{grid-template-columns:1fr;}
}
.form-group input:focus,.form-group select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(74,222,128,.15);}
.btn{
  background:linear-gradient(180deg,var(--accent-bright) 0%,var(--accent) 100%);
  color:#0a0d12;border:none;padding:9px 18px;border-radius:8px;
  font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
  box-shadow:0 2px 8px rgba(74,222,128,.3);transition:all .2s var(--ease);
  white-space:nowrap;
}
.btn:hover{transform:translateY(-1px);box-shadow:0 4px 16px rgba(74,222,128,.4);}
.btn-sm{padding:6px 12px;font-size:12px;}
.btn-danger{background:linear-gradient(180deg,var(--red-bright),var(--red));color:#fff;box-shadow:0 2px 8px rgba(229,0,26,.3);}
.btn-ghost{background:transparent;color:var(--muted);border:1px solid var(--border);box-shadow:none;}
.btn-ghost:hover{color:var(--text);border-color:var(--border-strong);background:var(--surface2);}

/* TABLE */
.data-table{width:100%;border-collapse:collapse;}
.data-table th{
  font-size:11px;font-weight:600;color:var(--muted);text-align:left;
  padding:8px 10px;border-bottom:1px solid var(--border);
  text-transform:uppercase;letter-spacing:.06em;background:rgba(0,0,0,.15);
}
.data-table td{padding:8px 10px;border-bottom:1px solid var(--border);font-size:13px;}
.data-table tr:last-child td{border:none;}
.data-table tr:hover td{background:var(--surface2);}

/* MEAL BADGE */
.meal-badge{
  display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:600;
  text-transform:uppercase;letter-spacing:.04em;
}
.meal-breakfast{background:rgba(251,191,36,.15);color:#fbbf24;border:1px solid rgba(251,191,36,.3);}
.meal-lunch{background:rgba(59,130,246,.15);color:#60a5fa;border:1px solid rgba(59,130,246,.3);}
.meal-dinner{background:rgba(168,85,247,.15);color:#c084fc;border:1px solid rgba(168,85,247,.3);}
.meal-snack{background:rgba(74,222,128,.12);color:#4ade80;border:1px solid rgba(74,222,128,.3);}
.meal-other{background:var(--surface2);color:var(--muted);border:1px solid var(--border);}

/* DATE NAV */
.date-nav{display:flex;align-items:center;gap:12px;margin-bottom:1.25rem;}
.date-nav a{
  padding:6px 12px;border-radius:8px;font-size:13px;font-weight:500;
  color:var(--muted);border:1px solid var(--border);transition:all .2s;
}
.date-nav a:hover{color:var(--text);border-color:var(--border-strong);background:var(--surface2);}
.date-nav .today{font-size:16px;font-weight:600;color:var(--text-strong);}

/* LOGIN */
.login-wrap{max-width:420px;margin:4rem auto;padding:0 1rem;text-align:center;}
.login-wrap h1{font-size:24px;font-weight:600;color:var(--text-strong);margin-bottom:.5rem;}
.login-wrap .sub{color:var(--muted);font-size:14px;margin-bottom:2rem;}
.login-card{
  background:linear-gradient(180deg,var(--surface) 0%,var(--bg-elev) 100%);
  border:1px solid var(--border);border-radius:var(--radius);padding:2rem;
}

/* RESPONSIVE */
@media(max-width:600px){
  .container{padding:1rem;}
  .form-row{flex-direction:column;}
  .form-group{min-width:100%!important;}
  .nav{padding:0 1rem;}
  .hide-mobile{display:none!important;}
}

/* QUICK ADD */
.quick-add{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:.5rem;
  margin-bottom:1rem;
}
.quick-chip{
  background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:8px 10px;font-size:12px;cursor:pointer;transition:all .2s;
  display:flex;flex-direction:column;
}
.quick-chip:hover{border-color:var(--accent);background:rgba(74,222,128,.06);}
.quick-chip .qname{font-weight:500;color:var(--text-strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.quick-chip .qmeta{color:var(--muted);font-size:11px;margin-top:2px;}
</style>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
"""


I18N = """
<script>
var TRANSLATIONS = {
  // Nav
  'Dashboard': 'Pradžia',
  'Products': 'Produktai',
  'History': 'Istorija',
  // Main page
  'Quick Add': 'Greitas pridėjimas',
  'Product': 'Produktas',
  'Select...': 'Pasirinkite...',
  'Grams': 'Gramai',
  'Meal': 'Valgymas',
  'Breakfast': 'Pusryčiai',
  'breakfast': 'Pusryčiai',
  'Lunch': 'Pietūs',
  'lunch': 'Pietūs',
  'Dinner': 'Vakarienė',
  'dinner': 'Vakarienė',
  'Snack': 'Užkandis',
  'snack': 'Užkandis',
  'Other': 'Kita',
  'other': 'Kita',
  '+ Add': '+ Pridėti',
  "Today\'s Log": "Dienos įrašai",
  'Food': 'Maistas',
  'Total': 'Viso',
  'fat': 'riebalai',
  'protein': 'baltymai',
  'carbs': 'angliavandeniai',
  'Fat': 'Riebalai',
  'Protein': 'Baltymai',
  'Carbs': 'Angliavandeniai',
  'Fat (g)': 'Riebalai (g)',
  'Protein (g)': 'Baltymai (g)',
  'Carbs (g)': 'Angliavandeniai (g)',
  '7-Day Trend': '7 dienų tendencija',
  'Daily Goals': 'Dienos tikslai',
  'Save Goals': 'Išsaugoti tikslus',
  'Today': 'Šiandien',
  'No products yet. Add your first food product to start tracking.': 'Produktų dar nėra. Pridėkite pirmą maisto produktą.',
  '+ Add Products': '+ Pridėti produktus',
  // Products page
  'Add New Product': 'Pridėti naują produktą',
  'Enter values from the nutrition label, or scan it with your camera.': 'Įveskite reikšmes iš etiketės arba nuskenuokite brūkšninį kodą.',
  'Scan Barcode': 'Skenuoti kodą',
  'Or type barcode...': 'Arba įveskite kodą...',
  'Filter products...': 'Filtruoti produktus...',
  'Search products...': 'Ieškoti produktų...',
  'Look up': 'Ieškoti',
  'Product Name': 'Produkto pavadinimas',
  'Per (g)': 'Kiekis (g)',
  'Your Products': 'Jūsų produktai',
  'Name': 'Pavadinimas',
  'Per': 'Kiekis',
  'Edit Product': 'Redaguoti produktą',
  'Save Changes': 'Išsaugoti pakeitimus',
  'Processing...': 'Apdorojama...',
  // History page
  'Daily History (Last 30 Days)': 'Dienų istorija (paskutinės 30 dienų)',
  'Date': 'Data',
  'Items': 'Įrašai',
  'View': 'Peržiūrėti',
  'No entries yet. Start logging food on the dashboard.': 'Įrašų dar nėra. Pradėkite sekti maistą pradžios puslapyje.',
  // Login
  'Track your nutrition with ease': 'Sekite mitybą lengvai',
  'Sign in with Google': 'Prisijungti su Google',
  'Continue with Demo': 'Tęsti su Demo',
  // Scale
  'Connect BLE scale': 'Prijungti BLE svarstykles',
  'Scale disconnected': 'Svarstyklės atjungtos',
  // Barcode results
  'Product not found in database. Try entering values manually.': 'Produktas nerastas duomenų bazėje. Įveskite reikšmes rankiniu būdu.',
  'Could not access camera. Try typing the barcode number.': 'Nepavyko pasiekti kameros. Pabandykite įvesti brūkšninio kodo numerį.',
  'Found:': 'Rasta:',
  'Remove': 'Pašalinti',
  'Cancel': 'Atšaukti',
  'Stop Scanner': 'Sustabdyti',
  'Kcal': 'Kcal',
  'Quick Add': 'Greitas pridėjimas',
  'e.g. Chicken Breast': 'pvz. Vištienos krūtinėlė',
  'Load default products': 'Įkelti standartinius produktus'
};

function getLang(){
  var c = document.cookie.match('(^|;)\\s*lang=([^;]+)');
  return c ? c[2] : 'en';
}
function setLang(lang){
  document.cookie = 'lang=' + lang + ';path=/;max-age=31536000;SameSite=Lax';
}
function toggleLang(){
  var lang = getLang() === 'en' ? 'lt' : 'en';
  setLang(lang);
  applyLang(lang);
}
function applyLang(lang){
  var btn = document.getElementById('langBtn');
  if(btn) btn.textContent = lang === 'en' ? 'LT' : 'EN';
  
  // Translate elements with data-i18n attribute
  document.querySelectorAll('[data-i18n]').forEach(function(el){
    var key = el.getAttribute('data-i18n');
    if(lang === 'lt' && TRANSLATIONS[key]){
      el.textContent = TRANSLATIONS[key];
    } else {
      el.textContent = key;
    }
  });
  // Translate placeholders
  document.querySelectorAll('[data-i18n-ph]').forEach(function(el){
    var key = el.getAttribute('data-i18n-ph');
    if(lang === 'lt' && TRANSLATIONS[key]){
      el.placeholder = TRANSLATIONS[key];
    } else {
      el.placeholder = key;
    }
  });
  // Translate option elements
  document.querySelectorAll('[data-i18n-opt]').forEach(function(el){
    var key = el.getAttribute('data-i18n-opt');
    if(lang === 'lt' && TRANSLATIONS[key]){
      el.textContent = TRANSLATIONS[key];
    } else {
      el.textContent = key;
    }
  });
  // Translate title attributes
  document.querySelectorAll('[data-i18n-title]').forEach(function(el){
    var key = el.getAttribute('data-i18n-title');
    if(lang === 'lt' && TRANSLATIONS[key]){
      el.title = TRANSLATIONS[key];
    } else {
      el.title = key;
    }
  });
  // Update date label for locale
  var dateLabel = document.getElementById('dateLabel');
  if(dateLabel && dateLabel.getAttribute('data-date')){
    var d = new Date(dateLabel.getAttribute('data-date'));
    var locale = lang === 'lt' ? 'lt-LT' : 'en-US';
    var label = d.toLocaleDateString(locale, {weekday:'short', month:'short', day:'numeric'});
    var today = new Date();
    if(d.toISOString().slice(0,10) === today.toISOString().slice(0,10)){
      label = (lang === 'lt' ? 'Šiandien' : 'Today') + ' — ' + label;
    }
    dateLabel.textContent = label;
  }
}
// Apply on load
document.addEventListener('DOMContentLoaded', function(){ applyLang(getLang()); });
</script>
"""

NAV = """
<nav class="nav">
  <a href="/" class="nav-brand">
    <div class="nav-brand-icon">🔥</div>
    <span class="nav-brand-name">CalorieTracker</span>
  </a>
  <div class="nav-links">
    <a href="/" class="nav-link {{ 'active' if active=='dashboard' }}">📊 <span class="hide-mobile" data-i18n="Dashboard">Dashboard</span></a>
    <a href="/products" class="nav-link {{ 'active' if active=='products' }}">📦 <span class="hide-mobile" data-i18n="Products">Products</span></a>
    <a href="/history" class="nav-link {{ 'active' if active=='history' }}">📅 <span class="hide-mobile" data-i18n="History">History</span></a>
    {% if user and user.picture %}<img src="{{ user.picture }}" class="nav-avatar" referrerpolicy="no-referrer">{% endif %}
    <button onclick="toggleLang()" id="langBtn" style="background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:2px 8px;color:var(--accent);font-size:11px;font-weight:600;cursor:pointer;margin-left:4px;">EN</button>
    <a href="/logout" class="nav-link">↗</a>
  </div>
</nav>
""" + I18N

LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CalorieTracker — Login</title>""" + STYLE + """
<script src="https://accounts.google.com/gsi/client" async defer></script>
</head><body>
<nav class="nav"><div class="nav-brand"><div class="nav-brand-icon">🔥</div><span class="nav-brand-name">CalorieTracker</span></div></nav>
<div class="login-wrap">
  <h1>Track Your Nutrition</h1>
  <p class="sub">Log calories, protein, fat & carbs from food labels. See daily totals and weekly trends.</p>
  <div class="login-card">
    <div id="g_id_onload"
         data-client_id="{{ google_client_id }}"
         data-callback="handleCredentialResponse"
         data-auto_prompt="false"></div>
    <div class="g_id_signin" data-type="standard" data-size="large" data-theme="filled_black" data-text="signin_with" data-shape="pill" data-width="300"></div>
  </div>
  <script>
  function handleCredentialResponse(response) {
    fetch("/auth/google", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({credential: response.credential}),
      credentials: "same-origin"
    }).then(function(r){ window.location.href = "/"; });
  }
  </script>
  <div style="margin-top:1.5rem;padding-top:1.5rem;border-top:1px solid var(--border);text-align:center;">
    <p style="color:var(--muted);font-size:12px;margin-bottom:0.75rem;">Google login not working? Sign in with email:</p>
    <form method="POST" action="/auth/dev" style="display:flex;gap:8px;max-width:300px;margin:0 auto;">
      <input name="email" type="email" placeholder="your@email.com" required style="flex:1;padding:8px 12px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;">
      <button type="submit" class="btn" style="white-space:nowrap;">Sign In</button>
    </form>
  </div>
</div>
</body></html>"""

LOGIN_NO_OAUTH = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CalorieTracker — Login</title>""" + STYLE + """</head><body>
<nav class="nav"><div class="nav-brand"><div class="nav-brand-icon">🔥</div><span class="nav-brand-name">CalorieTracker</span></div></nav>
<div class="login-wrap">
  <h1>Track Your Nutrition</h1>
  <p class="sub">Google OAuth not configured. Using dev login.</p>
  <div class="login-card">
    <form method="POST" action="/auth/dev">
      <div class="form-group" style="margin-bottom:1rem">
        <label>Email</label>
        <input name="email" value="dev@localhost" required>
      </div>
      <button type="submit" class="btn" style="width:100%">Sign In (Dev Mode)</button>
    </form>
  </div>
</div>
</body></html>"""

MAIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CalorieTracker</title>""" + STYLE + """</head><body>
""" + NAV.replace("active=='dashboard'", "True") + """
<div class="container">

<!-- DATE NAV -->
<div class="date-nav">
  {% set prev = (today | replace('-','') | int) %}
  <a href="/?date={{ (today[:10] | string) }}" id="prevDay">◀</a>
  <span class="today" id="dateLabel">{{ today }}</span>
  <a href="/?date={{ today }}" id="nextDay">▶</a>
  <a href="/" style="margin-left:auto" data-i18n="Today">Today</a>
</div>
<script>
(function(){
  var d = new Date('{{ today }}');
  var prev = new Date(d); prev.setDate(prev.getDate()-1);
  var next = new Date(d); next.setDate(next.getDate()+1);
  var fmt = function(dt){ return dt.toISOString().slice(0,10); };
  document.getElementById('prevDay').href = '/?date=' + fmt(prev);
  document.getElementById('nextDay').href = '/?date=' + fmt(next);
  var loc = getLang && getLang() === 'lt' ? 'lt-LT' : 'en-US'; var label = d.toLocaleDateString(loc, {weekday:'short', month:'short', day:'numeric'});
  if(fmt(d) === fmt(new Date())) label = (loc === 'lt-LT' ? 'Šiandien' : 'Today') + ' — ' + label;
  document.getElementById('dateLabel').textContent = label; document.getElementById('dateLabel').setAttribute('data-date', '{{ today }}');
})();
</script>

<!-- DAILY TOTALS -->
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-num kcal-color" id="totalKcal">{{ totals.kcal }}</div>
    <div class="stat-lbl"><span data-i18n="Kcal">kcal</span>{% if goals %} / {{ goals.kcal|int }}{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill kcal-fill" style="width:{{ [totals.kcal/goals.kcal*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num fat-color" id="totalFat">{{ totals.fat }}g</div>
    <div class="stat-lbl"><span data-i18n="Fat">fat</span>{% if goals %} / {{ goals.fat|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill fat-fill" style="width:{{ [totals.fat/goals.fat*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num protein-color" id="totalProtein">{{ totals.protein }}g</div>
    <div class="stat-lbl"><span data-i18n="Protein">protein</span>{% if goals %} / {{ goals.protein|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill protein-fill" style="width:{{ [totals.protein/goals.protein*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num carbs-color" id="totalCarbs">{{ totals.carbs }}g</div>
    <div class="stat-lbl"><span data-i18n="Carbs">carbs</span>{% if goals %} / {{ goals.carbs|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill carbs-fill" style="width:{{ [totals.carbs/goals.carbs*100, 100]|min }}%"></div></div>{% endif %}
  </div>
</div>

<!-- QUICK ADD -->
{% if products %}
<div class="card">
  <div class="card-title" data-i18n="Quick Add">Quick Add</div>
  <div class="quick-add">
    {% for p in top_products %}
    <div class="quick-chip" onclick="quickAdd({{ p.id }}, '{{ p.name|e }}')">
      <span class="qname">{{ p.name }}</span>
      <span class="qmeta">{{ p.kcal|int }} kcal/{{ p.per_grams|int }}g</span>
    </div>
    {% endfor %}
  </div>
  <form method="POST" action="/api/log" class="log-form-row" id="logForm">
    <input type="hidden" name="log_date" value="{{ today }}">
    <div class="form-group wide">
      <label data-i18n="Product">Product</label>
      <input type="hidden" name="product_id" id="productSelectValue" required>
      <div style="position:relative" id="productDropdown">
        <input type="text" id="productSearch" autocomplete="off" placeholder="Search products..." data-i18n-ph="Search products..." onclick="showProductList()" oninput="filterProductList()" style="width:100%;">
        <div id="productList" style="display:none;position:absolute;left:0;right:0;top:100%;max-height:200px;overflow-y:auto;background:var(--surface2);border:1px solid var(--border);border-radius:0 0 8px 8px;z-index:100;">
          {% for p in products %}<div class="pl-item" data-id="{{ p.id }}" data-name="{{ p.name }}" onclick="pickProduct(this)">{{ p.name }} ({{ p.kcal|int }} kcal/{{ p.per_grams|int }}g)</div>{% endfor %}
        </div>
      </div>
    </div>
    <div class="form-group">
      <label data-i18n="Grams">Grams</label>
      <div style="display:flex;gap:4px;align-items:stretch;">
        <input name="grams" type="number" step="0.1" min="0" id="gramsInput" required placeholder="100" style="flex:1;min-width:0;">
        <button type="button" id="scaleBtn" onclick="toggleScale()" style="width:38px;flex-shrink:0;padding:0;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;" title="Connect BLE scale" data-i18n-title="Connect BLE scale">&#9878;</button>
      </div>
      <div id="scaleStatus" style="display:none;margin-top:4px;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;"></div>
    </div>
    <div class="form-group">
      <label data-i18n="Meal">Meal</label>
      <select name="meal">
        <option value="breakfast" data-i18n-opt="Breakfast">Breakfast</option>
        <option value="lunch" data-i18n-opt="Lunch">Lunch</option>
        <option value="dinner" data-i18n-opt="Dinner">Dinner</option>
        <option value="snack" data-i18n-opt="Snack">Snack</option>
        <option value="other" data-i18n-opt="Other">Other</option>
      </select>
    </div>
    <button type="submit" class="btn" data-i18n="+ Add">+ Add</button>
  </form>
</div>
{% else %}
<div class="card" style="text-align:center;padding:2rem">
  <p style="color:var(--muted);margin-bottom:1rem" data-i18n="No products yet. Add your first food product to start tracking.">No products yet. Add your first food product to start tracking.</p>
  <a href="/products" class="btn" style="display:inline-block" data-i18n="+ Add Products">+ Add Products</a>
</div>
{% endif %}

<!-- TODAY'S LOG -->
<div class="card" id="todayLogCard" {% if not entries %}style="display:none"{% endif %}>
  <div class="card-title" data-i18n="Today's Log">Today\'s Log</div>
  <div style="overflow-x:auto">
  <table class="data-table" id="todayLogTable">
    <tr><th data-i18n="Food">Food</th><th data-i18n="Grams">Grams</th><th data-i18n="Meal">Meal</th><th data-i18n="Kcal">Kcal</th><th data-i18n="Fat">Fat</th><th data-i18n="Protein">Protein</th><th data-i18n="Carbs">Carbs</th><th></th></tr>
    {% if entries %}
    {% for e in entries %}
    <tr>
      <td style="font-weight:500;color:var(--text-strong)">{{ e.name }}</td>
      <td>{{ e.grams }}g</td>
      <td><span class="meal-badge meal-{{ e.meal }}" data-i18n-opt="{{ e.meal }}">{{ e.meal }}</span></td>
      <td class="kcal-color">{{ e.kcal }}</td>
      <td class="fat-color">{{ e.fat }}g</td>
      <td class="protein-color">{{ e.protein }}g</td>
      <td class="carbs-color">{{ e.carbs }}g</td>
      <td><form method="POST" action="/api/log/{{ e.id }}/delete" style="display:inline"><button type="submit" class="btn-ghost btn-sm" title="Remove" data-i18n-title="Remove">✕</button></form></td>
    </tr>
    {% endfor %}
    {% endif %}
    <tr id="logTotalRow" style="font-weight:600;border-top:2px solid var(--border-strong)">
      <td colspan="3" style="color:var(--text-strong)" data-i18n="Total">Total</td>
      <td class="kcal-color" id="logTotalKcal">{{ totals.kcal }}</td>
      <td class="fat-color" id="logTotalFat">{{ totals.fat }}g</td>
      <td class="protein-color" id="logTotalProtein">{{ totals.protein }}g</td>
      <td class="carbs-color" id="logTotalCarbs">{{ totals.carbs }}g</td>
      <td></td>
    </tr>
  </table>
  </div>
</div>

<!-- WEEK CHART -->
<div class="card">
  <div class="card-title" data-i18n="7-Day Trend">7-Day Trend</div>
  <canvas id="weekChart" height="200"></canvas>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
var wdata = {{ week_data|safe }};
new Chart(document.getElementById('weekChart'), {
  type: 'bar',
  data: {
    labels: wdata.map(function(d){ var dt=new Date(d.date); return dt.toLocaleDateString(getLang()==='lt'?'lt-LT':'en-US',{month:'short',day:'numeric'}); }),
    datasets: [
      {label:'Kcal', data:wdata.map(function(d){return d.kcal;}), backgroundColor:'rgba(74,222,128,.6)', borderRadius:4, yAxisID:'y'},
      {label:(getLang()==='lt'?'Baltymai':'Protein'), data:wdata.map(function(d){return d.protein;}), backgroundColor:'rgba(59,130,246,.6)', borderRadius:4, yAxisID:'y1'},
      {label:(getLang()==='lt'?'Riebalai':'Fat'), data:wdata.map(function(d){return d.fat;}), backgroundColor:'rgba(245,158,11,.6)', borderRadius:4, yAxisID:'y1'},
      {label:(getLang()==='lt'?'Angliavandeniai':'Carbs'), data:wdata.map(function(d){return d.carbs;}), backgroundColor:'rgba(167,139,250,.6)', borderRadius:4, yAxisID:'y1'}
    ]
  },
  options: {
    responsive:true, interaction:{mode:'index',intersect:false},
    plugins:{legend:{labels:{color:'#8b95a8',font:{size:11}}}},
    scales:{
      x:{ticks:{color:'#5f6776'},grid:{color:'rgba(255,255,255,.04)'}},
      y:{position:'left',ticks:{color:'#4ade80'},grid:{color:'rgba(255,255,255,.04)'},title:{display:true,text:'Kcal',color:'#4ade80'}},
      y1:{position:'right',ticks:{color:'#8b95a8'},grid:{display:false},title:{display:true,text:(getLang()==='lt'?'Gramai':'Grams'),color:'#8b95a8'}}
    }
  }
});
document.addEventListener('DOMContentLoaded', function(){
  var form = document.getElementById('logForm');
  if(form){
    form.addEventListener('submit', function(e){
      if(!scaleConnected){ return; } // normal submit if scale not connected
      e.preventDefault();
      var productId = document.getElementById('productSelectValue').value;
      var grams = document.getElementById('gramsInput').value;
      var meal = form.querySelector('select[name="meal"]').value;
      var logDate = form.querySelector('input[name="log_date"]').value;
      if(!productId || !grams){ return; }
      fetch('/api/log', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({product_id: productId, grams: grams, meal: meal, log_date: logDate}),
        credentials: 'same-origin'
      }).then(function(r){ return r.json(); })
      .then(function(data){
        if(data.ok){
          var lang = getLang();
          var name = document.getElementById('productSearch').value;
          showToast((lang==='lt' ? 'Prideta: ' : 'Added: ') + name + ' ' + grams + 'g');
          document.getElementById('gramsInput').value = '';
          document.getElementById('productSearch').value = '';
          document.getElementById('productSelectValue').value = '';
          // Add row to today's log
          if(data.entry){
            var card = document.getElementById('todayLogCard');
            card.style.display = '';
            var table = document.getElementById('todayLogTable');
            var totalRow = document.getElementById('logTotalRow');
            var tr = document.createElement('tr');
            tr.style.animation = 'fadeIn .3s ease';
            tr.innerHTML = '<td style="font-weight:500;color:var(--text-strong)">' + data.entry.name + '</td>'
              + '<td>' + data.entry.grams + 'g</td>'
              + '<td><span class="meal-badge meal-' + data.entry.meal + '">' + data.entry.meal + '</span></td>'
              + '<td class="kcal-color">' + data.entry.kcal + '</td>'
              + '<td class="fat-color">' + data.entry.fat + 'g</td>'
              + '<td class="protein-color">' + data.entry.protein + 'g</td>'
              + '<td class="carbs-color">' + data.entry.carbs + 'g</td>'
              + '<td></td>';
            table.insertBefore(tr, totalRow);
          }
          // Update totals display
          if(data.totals){
            var kcalEl = document.getElementById('totalKcal');
            if(kcalEl) kcalEl.textContent = Math.round(data.totals.kcal);
            var fatEl = document.getElementById('totalFat');
            if(fatEl) fatEl.textContent = data.totals.fat.toFixed(1) + 'g';
            var proteinEl = document.getElementById('totalProtein');
            if(proteinEl) proteinEl.textContent = data.totals.protein.toFixed(1) + 'g';
            var carbsEl = document.getElementById('totalCarbs');
            if(carbsEl) carbsEl.textContent = data.totals.carbs.toFixed(1) + 'g';
            // Update log table totals too
            var ltk = document.getElementById('logTotalKcal');
            if(ltk) ltk.textContent = data.totals.kcal;
            var ltf = document.getElementById('logTotalFat');
            if(ltf) ltf.textContent = data.totals.fat.toFixed(1) + 'g';
            var ltp = document.getElementById('logTotalProtein');
            if(ltp) ltp.textContent = data.totals.protein.toFixed(1) + 'g';
            var ltc = document.getElementById('logTotalCarbs');
            if(ltc) ltc.textContent = data.totals.carbs.toFixed(1) + 'g';
            // Update progress bars
            var fills = document.querySelectorAll('.stat-fill');
            fills.forEach(function(f){
              var cls = f.className;
              var goals = {kcal:0,fat:0,protein:0,carbs:0};
              var gs = document.querySelectorAll('.stat-lbl');
              gs.forEach(function(lbl){
                var txt = lbl.textContent;
                var m = txt.match(/\/\s*(\d+)/);
                if(m){
                  if(lbl.parentElement.querySelector('.kcal-fill')) goals.kcal = parseInt(m[1]);
                  if(lbl.parentElement.querySelector('.fat-fill')) goals.fat = parseInt(m[1]);
                  if(lbl.parentElement.querySelector('.protein-fill')) goals.protein = parseInt(m[1]);
                  if(lbl.parentElement.querySelector('.carbs-fill')) goals.carbs = parseInt(m[1]);
                }
              });
              if(cls.indexOf('kcal-fill')>-1 && goals.kcal) f.style.width = Math.min(data.totals.kcal/goals.kcal*100,100)+'%';
              if(cls.indexOf('fat-fill')>-1 && goals.fat) f.style.width = Math.min(data.totals.fat/goals.fat*100,100)+'%';
              if(cls.indexOf('protein-fill')>-1 && goals.protein) f.style.width = Math.min(data.totals.protein/goals.protein*100,100)+'%';
              if(cls.indexOf('carbs-fill')>-1 && goals.carbs) f.style.width = Math.min(data.totals.carbs/goals.carbs*100,100)+'%';
            });
          }
        }
      }).catch(function(err){
        showToast('Error: ' + (err.message || err));
      });
    });
  }
});
function showToast(msg){
  var t = document.getElementById('ajaxToast');
  if(!t){
    t = document.createElement('div');
    t.id = 'ajaxToast';
    t.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;padding:10px 20px;border-radius:10px;font-size:13px;font-weight:500;z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none;';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = '1';
  clearTimeout(t._timer);
  t._timer = setTimeout(function(){ t.style.opacity = '0'; }, 2500);
}
function quickAdd(id, name){
  document.getElementById('productSelectValue').value = id;
  document.getElementById('productSearch').value = name;
  document.getElementById('productList').style.display = 'none';
  document.getElementById('gramsInput').focus();
}
function showProductList(){
  document.getElementById('productList').style.display='block';
  filterProductList();
}
function filterProductList(){
  var q=document.getElementById('productSearch').value.toLowerCase();
  var items=document.getElementById('productList').querySelectorAll('.pl-item');
  for(var i=0;i<items.length;i++){
    items[i].style.display=items[i].getAttribute('data-name').toLowerCase().indexOf(q)>-1?'':'none';
  }
}
function pickProduct(el){
  document.getElementById('productSelectValue').value=el.getAttribute('data-id');
  document.getElementById('productSearch').value=el.getAttribute('data-name');
  document.getElementById('productList').style.display='none';
}
document.addEventListener('click',function(e){
  var dd=document.getElementById('productDropdown');
  if(dd && !dd.contains(e.target)){
    document.getElementById('productList').style.display='none';
  }
});
</script>

<!-- GOALS -->
<div class="card">
  <div class="card-title" data-i18n="Daily Goals">Daily Goals</div>
  <form method="POST" action="/api/goals" class="form-row">
    <div class="form-group"><label data-i18n="Kcal">Kcal</label><input name="kcal" type="number" value="{{ goals.kcal|int if goals else 2000 }}"></div>
    <div class="form-group"><label data-i18n="Fat (g)">Fat (g)</label><input name="fat" type="number" value="{{ goals.fat|int if goals else 65 }}"></div>
    <div class="form-group"><label data-i18n="Protein (g)">Protein (g)</label><input name="protein" type="number" value="{{ goals.protein|int if goals else 50 }}"></div>
    <div class="form-group"><label data-i18n="Carbs (g)">Carbs (g)</label><input name="carbs" type="number" value="{{ goals.carbs|int if goals else 300 }}"></div>
    <button type="submit" class="btn btn-ghost" data-i18n="Save Goals">Save Goals</button>
  </form>
</div>


<script>
var bleDevice = null;
var bleServer = null;
var scaleConnected = false;

// Standard Bluetooth Weight Scale Service UUIDs (full string form for Bluefy compatibility)
var WEIGHT_SCALE_SERVICE = '0000181d-0000-1000-8000-00805f9b34fb';
var WEIGHT_MEASUREMENT_CHAR = '00002a9d-0000-1000-8000-00805f9b34fb';
var DEVICE_INFO_SERVICE = '0000180a-0000-1000-8000-00805f9b34fb';

// Common custom UUIDs used by kitchen scales
var CUSTOM_SERVICES = [
  '00001910-0000-1000-8000-00805f9b34fb',  // Etekcity-style
  '0000fff0-0000-1000-8000-00805f9b34fb',  // Common Chinese scales
  '0000ffe0-0000-1000-8000-00805f9b34fb',  // Another common one
  '00001820-0000-1000-8000-00805f9b34fb',  // Internet Protocol Support
];
var CUSTOM_NOTIFY_CHARS = [
  '00002c12-0000-1000-8000-00805f9b34fb',
  '0000fff1-0000-1000-8000-00805f9b34fb',
  '0000fff4-0000-1000-8000-00805f9b34fb',
  '0000ffe1-0000-1000-8000-00805f9b34fb',
  '0000ffe4-0000-1000-8000-00805f9b34fb',
];

function scaleLog(msg, showInUI){
  if(showInUI !== false){
    var el = document.getElementById('scaleStatus');
    if(el){ el.style.display = 'block'; el.textContent = msg; }
  }
  try { fetch('/api/jslog', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({msg:'[SCALE] ' + msg})}); } catch(e){}
}

async function toggleScale(){
  if(scaleConnected){
    disconnectScale();
    return;
  }
  if(!navigator.bluetooth){
    scaleLog('Web Bluetooth not supported in this browser. Use Chrome on Android for scale.');
    return;
  }
  try {
    var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    var isBluefy = /Bluefy/.test(navigator.userAgent);
    if(isIOS && !isBluefy){
      scaleLog('iOS requires Bluefy browser for BLE. Install from App Store.');
      return;
    }
    if(isIOS && isBluefy){
      scaleLog('Bluefy detected - attempting BLE connection...');
    }
    scaleLog('Requesting BLE device...');
    // Request device - try standard weight service first, accept all
    bleDevice = await navigator.bluetooth.requestDevice({
      acceptAllDevices: true,
      optionalServices: [WEIGHT_SCALE_SERVICE, DEVICE_INFO_SERVICE].concat(CUSTOM_SERVICES)
    });
    scaleLog('Connecting to ' + (bleDevice.name || 'scale') + '...');
    bleDevice.addEventListener('gattserverdisconnected', onScaleDisconnected);
    if(!bleDevice.gatt){
      scaleLog('Device does not support GATT');
      return;
    }
    bleServer = await bleDevice.gatt.connect();
    document.getElementById('scaleBtn').style.background = 'rgba(74,222,128,.2)';
    document.getElementById('scaleBtn').style.borderColor = '#4ade80';
    scaleConnected = true;

    // Try standard weight scale service first
    var found = await tryStandardWeightService();
    if(!found){
      scaleLog('Standard service not found, scanning all services...');
      found = await tryCustomServices();
    }
    if(!found){
      found = await tryDiscoverAll();
    }
    if(!found){
      scaleLog('Connected but could not find weight data. Check docker logs for discovered services.');
    }
  } catch(err) {
    scaleLog('Connection failed: ' + (err.message || (typeof err === 'string' ? err : JSON.stringify(err))));
    scaleConnected = false;
  }
}

async function tryStandardWeightService(){
  try {
    var service = await bleServer.getPrimaryService(WEIGHT_SCALE_SERVICE);
    scaleLog('Found standard weight service!');
    var char = await service.getCharacteristic(WEIGHT_MEASUREMENT_CHAR);
    await char.startNotifications();
    char.addEventListener('characteristicvaluechanged', handleStandardWeight);
    scaleLog('Listening for weight (standard)...');
    return true;
  } catch(e){
    scaleLog('No standard weight service: ' + e.message);
    return false;
  }
}

async function tryCustomServices(){
  for(var s = 0; s < CUSTOM_SERVICES.length; s++){
    try {
      var service = await bleServer.getPrimaryService(CUSTOM_SERVICES[s]);
      scaleLog('Found service: ' + CUSTOM_SERVICES[s]);
      var chars = await service.getCharacteristics();
      for(var c = 0; c < chars.length; c++){
        scaleLog('  Char: ' + chars[c].uuid + ' props: ' + JSON.stringify(chars[c].properties));
        if(chars[c].properties.notify || chars[c].properties.indicate){
          await chars[c].startNotifications();
          chars[c].addEventListener('characteristicvaluechanged', handleRawWeight);
          scaleLog('Listening on ' + chars[c].uuid + '...');
          return true;
        }
      }
    } catch(e){ /* service not found, try next */ }
  }
  return false;
}

async function tryDiscoverAll(){
  try {
    var services = await bleServer.getPrimaryServices();
    scaleLog('Discovered ' + services.length + ' services');
    for(var s = 0; s < services.length; s++){
      scaleLog('Service: ' + services[s].uuid);
      try {
        var chars = await services[s].getCharacteristics();
        for(var c = 0; c < chars.length; c++){
          var p = chars[c].properties;
          scaleLog('  ' + chars[c].uuid + ' R:' + p.read + ' W:' + p.write + ' N:' + p.notify + ' I:' + p.indicate);
          if(p.notify || p.indicate){
            await chars[c].startNotifications();
            chars[c].addEventListener('characteristicvaluechanged', handleRawWeight);
            scaleLog('Subscribed to ' + chars[c].uuid);
            return true;
          }
        }
      } catch(e2){ scaleLog('  Error reading chars: ' + e2.message); }
    }
  } catch(e){
    scaleLog('Cannot discover services: ' + e.message);
  }
  return false;
}

function handleStandardWeight(event){
  // Bluetooth Weight Measurement format (0x2A9D):
  // Byte 0: Flags (bit 0: 0=SI/kg, 1=Imperial/lb)
  // Bytes 1-2: Weight (uint16, resolution 0.005kg or 0.01lb)
  var data = event.target.value;
  var flags = data.getUint8(0);
  var raw = data.getUint16(1, true);
  var weight;
  if(flags & 0x01){
    weight = raw * 0.01; // pounds
    weight = weight * 453.592; // convert to grams
  } else {
    weight = raw * 5; // 0.005 kg = 5 grams resolution
  }
  updateWeight(weight);
}

function handleRawWeight(event){
  // Arboleaf kitchen scale protocol (reverse-engineered from packet diffs):
  // Weight lives at bytes 9-10, big-endian uint16, 0.1g resolution.
  // Bytes 0-8 and 11-16 are constant across readings (device/status fields);
  // byte 17 changes unpredictably and looks like a checksum.
  var data = event.target.value;
  var bytes = [];
  for(var i = 0; i < data.byteLength; i++) bytes.push(data.getUint8(i));
  scaleLog('Raw: [' + bytes.join(', ') + ']', false); // server log only, keep out of the UI

  if(data.byteLength >= 11){
    var w = data.getUint16(9, false); // bytes 9-10, big-endian
    updateWeight(w / 10);
    return;
  }

  scaleLog('Packet too short to parse weight (' + data.byteLength + ' bytes)', 'ERROR');
}

function updateWeight(grams){
  var rounded = Math.round(grams);
  if(rounded <= 0) return;
  document.getElementById('gramsInput').value = rounded;
  scaleLog('Weight: ' + rounded + 'g');
}

function disconnectScale(){
  if(bleDevice && bleDevice.gatt.connected){
    bleDevice.gatt.disconnect();
  }
  onScaleDisconnected();
}

function onScaleDisconnected(){
  scaleConnected = false;
  document.getElementById('scaleBtn').style.background = 'var(--surface2)';
  document.getElementById('scaleBtn').style.borderColor = 'var(--border)';
  scaleLog('Scale disconnected');
}
</script>
</div></body></html>"""

PRODUCTS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Products — CalorieTracker</title>""" + STYLE + """
<style>
.pl-item{padding:10px 14px;cursor:pointer;font-size:13px;color:var(--text);border-bottom:1px solid var(--border);margin:2px 4px;border-radius:6px;}
.pl-item:hover,.pl-item.active{background:var(--accent);color:#fff;}
.pl-item:last-child{border-bottom:none;}
.scan-area{margin-bottom:1rem;}
.scan-btn{
  display:inline-flex;align-items:center;gap:8px;padding:10px 18px;
  background:var(--surface2);border:1px dashed var(--border-strong);border-radius:10px;
  color:var(--text);font-size:13px;font-weight:500;cursor:pointer;
  transition:all .2s var(--ease);font-family:inherit;width:100%;justify-content:center;
}
.scan-btn:hover{border-color:var(--accent);background:rgba(74,222,128,.06);color:var(--accent-bright);}
.scan-preview{margin-top:.75rem;position:relative;display:none;}
.scan-preview img{max-width:100%;max-height:300px;border-radius:8px;border:1px solid var(--border);}
.scan-preview video{max-width:100%;max-height:300px;border-radius:8px;border:1px solid var(--border);background:#000;}
.scan-status{
  margin-top:8px;padding:8px 12px;border-radius:8px;font-size:12px;
  background:var(--surface2);color:var(--muted);display:none;
}
.scan-status.active{display:flex;align-items:center;gap:8px;}
.scan-spinner{width:14px;height:14px;border-radius:50%;border:2px solid var(--surface3);border-top-color:var(--accent);animation:spin 1s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
.scan-actions{display:flex;gap:8px;margin-top:8px;}
.camera-controls{display:none;margin-top:8px;}
.camera-controls.active{display:flex;gap:8px;}
</style>
</head><body>
""" + NAV.replace("active=='products'", "True") + """
<div class="container">
<div class="card">
  <div class="card-title" data-i18n="Add New Product">Add New Product</div>
  <p style="color:var(--muted);font-size:12px;margin-bottom:.75rem" data-i18n="Enter values from the nutrition label, or scan it with your camera.">Enter values from the nutrition label, or scan it with your camera.</p>

  <!-- BARCODE SCANNER -->
  <div class="scan-area">
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <button type="button" class="scan-btn" style="flex:1 1 auto;" id="scanBarcodeBtn" onclick="startBarcodeScanner()" data-i18n="Scan Barcode">📊 Scan Barcode</button>
    </div>
    <div style="display:flex;gap:4px;margin-top:8px;">
      <input type="text" id="manualBarcode" placeholder="Or type barcode..." data-i18n-ph="Or type barcode..." style="flex:1;min-width:0;padding:8px 12px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;font-family:inherit;">
      <button type="button" class="btn btn-sm" onclick="lookupBarcode(document.getElementById('manualBarcode').value)" style="white-space:nowrap;flex-shrink:0;" data-i18n="Look up">Look up</button>
    </div>
    <div id="barcodeReader" style="display:none;margin-top:8px;"></div>
    <div class="scan-status" id="scanStatus"><div class="scan-spinner"></div><span id="scanText">Processing...</span></div>
  </div>

  <form method="POST" action="/api/products" class="form-row" id="addProductForm">
    <div class="form-group wide"><label data-i18n="Product Name">Product Name</label><input name="name" id="pName" required placeholder="e.g. Chicken Breast" data-i18n-ph="e.g. Chicken Breast"></div>
    <div class="form-group"><label data-i18n="Kcal">Kcal</label><input name="kcal" id="pKcal" type="number" step="0.1" required placeholder="165"></div>
    <div class="form-group"><label data-i18n="Fat (g)">Fat (g)</label><input name="fat" id="pFat" type="number" step="0.1" placeholder="3.6"></div>
    <div class="form-group"><label data-i18n="Protein (g)">Protein (g)</label><input name="protein" id="pProtein" type="number" step="0.1" placeholder="31"></div>
    <div class="form-group"><label data-i18n="Carbs (g)">Carbs (g)</label><input name="carbs" id="pCarbs" type="number" step="0.1" placeholder="0"></div>
    <div class="form-group"><label data-i18n="Per (g)">Per (g)</label><input name="per_grams" id="pPer" type="number" step="0.1" value="100" placeholder="100"></div>
    <button type="submit" class="btn" data-i18n="+ Add">+ Add</button>
  </form>
</div>

{% if products %}
<div class="card">
  <div class="card-title"><span data-i18n="Your Products">Your Products</span> ({{ products|length }})</div>
  <input type="text" id="productFilter" oninput="filterProducts()" placeholder="Filter products..." data-i18n-ph="Filter products..." style="width:100%;padding:8px 12px;margin-bottom:.75rem;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;font-family:inherit;">
  <div style="overflow-x:auto">
  <table class="data-table" id="productsTable">
    <tr><th data-i18n="Name">Name</th><th data-i18n="Kcal">Kcal</th><th data-i18n="Fat">Fat</th><th data-i18n="Protein">Protein</th><th data-i18n="Carbs">Carbs</th><th data-i18n="Per">Per</th><th></th></tr>
    {% for p in products %}
    <tr>
      <td style="font-weight:500;color:var(--text-strong)">{{ p.name }}</td>
      <td class="kcal-color">{{ p.kcal }}</td>
      <td class="fat-color">{{ p.fat }}g</td>
      <td class="protein-color">{{ p.protein }}g</td>
      <td class="carbs-color">{{ p.carbs }}g</td>
      <td>{{ p.per_grams }}g</td>
      <td>
        <button class="btn-ghost btn-sm" onclick="editProduct({{ p.id }}, '{{ p.name|e }}', {{ p.kcal }}, {{ p.fat }}, {{ p.protein }}, {{ p.carbs }}, {{ p.per_grams }})">✎</button>
        <form method="POST" action="/api/products/{{ p.id }}/delete" style="display:inline"
              onsubmit="return confirm('Delete {{ p.name|e }}?')">
          <button type="submit" class="btn-ghost btn-sm btn-danger">✕</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  </div>
</div>
{% endif %}

<!-- EDIT MODAL -->
<div id="editModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:2000;align-items:center;justify-content:center">
  <div class="card" style="max-width:500px;width:90%;margin:auto">
    <div class="card-title" data-i18n="Edit Product">Edit Product</div>
    <form method="POST" id="editForm" class="form-row" style="flex-direction:column;gap:.75rem">
      <div class="form-row">
        <div class="form-group wide"><label data-i18n="Name">Name</label><input name="name" id="eName" required></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label data-i18n="Kcal">Kcal</label><input name="kcal" id="eKcal" type="number" step="0.1"></div>
        <div class="form-group"><label data-i18n="Fat">Fat</label><input name="fat" id="eFat" type="number" step="0.1"></div>
        <div class="form-group"><label data-i18n="Protein">Protein</label><input name="protein" id="eProtein" type="number" step="0.1"></div>
        <div class="form-group"><label data-i18n="Carbs">Carbs</label><input name="carbs" id="eCarbs" type="number" step="0.1"></div>
        <div class="form-group"><label data-i18n="Per (g)">Per (g)</label><input name="per_grams" id="ePer" type="number" step="0.1"></div>
      </div>
      <div class="form-row">
        <button type="submit" class="btn">Save</button>
        <button type="button" class="btn btn-ghost" onclick="closeEdit()" data-i18n="Cancel">Cancel</button>
      </div>
    </form>
  </div>
</div>
<script>
function filterProducts(){
  var q = document.getElementById('productFilter').value.toLowerCase();
  var rows = document.getElementById('productsTable').querySelectorAll('tr');
  for(var i = 1; i < rows.length; i++){
    var name = rows[i].cells[0].textContent.toLowerCase();
    rows[i].style.display = name.indexOf(q) > -1 ? '' : 'none';
  }
}
function editProduct(id,n,k,f,p,c,pg){
  document.getElementById('editForm').action='/api/products/'+id;
  document.getElementById('eName').value=n;
  document.getElementById('eKcal').value=k;
  document.getElementById('eFat').value=f;
  document.getElementById('eProtein').value=p;
  document.getElementById('eCarbs').value=c;
  document.getElementById('ePer').value=pg;
  document.getElementById('editModal').style.display='flex';
}
function closeEdit(){document.getElementById('editModal').style.display='none';}
document.getElementById('editModal').addEventListener('click',function(e){if(e.target===this)closeEdit();});
</script>

<!-- Barcode Scanner -->
<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
var html5QrCode = null;
var scannerRunning = false;
var lastScannedCode = '';
var scanConfirmCount = 0;
var SCAN_CONFIRM_THRESHOLD = 2;

function jslog(msg, level){
  level = level || 'INFO';
  try { fetch('/api/jslog', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({msg:msg, level:level})}); } catch(e){}
}

function validateEAN13(code){
  if(!code || code.length !== 13 || !/^\d{13}$/.test(code)) return code.length === 8;
  var sum = 0;
  for(var i = 0; i < 12; i++){
    sum += parseInt(code[i]) * (i % 2 === 0 ? 1 : 3);
  }
  var check = (10 - (sum % 10)) % 10;
  return check === parseInt(code[12]);
}

function startBarcodeScanner(){
  var readerDiv = document.getElementById('barcodeReader');
  var btn = document.getElementById('scanBarcodeBtn');

  if(scannerRunning){
    stopBarcodeScanner();
    return;
  }

  readerDiv.style.display = 'block';
  btn.textContent = getLang()==='lt' ? '⏹ Sustabdyti' : '⏹ Stop Scanner';
  lastScannedCode = '';
  scanConfirmCount = 0;
  jslog('Starting barcode scanner');

  startHtml5Scanner(readerDiv, btn);
}

function startHtml5Scanner(readerDiv, btn){
  html5QrCode = new Html5Qrcode('barcodeReader', {
    formatsToSupport: [
      Html5QrcodeSupportedFormats.EAN_13,
      Html5QrcodeSupportedFormats.EAN_8,
      Html5QrcodeSupportedFormats.UPC_A,
      Html5QrcodeSupportedFormats.UPC_E
    ]
  });
  html5QrCode.start(
    { facingMode: 'environment' },
    { fps: 20, qrbox: { width: 350, height: 150 }, aspectRatio: 1.5, disableFlip: true, videoConstraints: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }, experimentalFeatures: { useBarCodeDetectorIfSupported: false } },
    function(decodedText){
      if(!validateEAN13(decodedText)) return;
      if(decodedText === lastScannedCode){
        scanConfirmCount++;
      } else {
        lastScannedCode = decodedText;
        scanConfirmCount = 1;
      }
      var pct = Math.round(scanConfirmCount / SCAN_CONFIRM_THRESHOLD * 100);
      showStatus((getLang()==='lt' ? 'Skenuojama... ' : 'Scanning... ') + pct + '%', 'ok');
      if(scanConfirmCount >= SCAN_CONFIRM_THRESHOLD){
        jslog('Barcode confirmed: ' + decodedText);
        stopBarcodeScanner();
        document.getElementById('manualBarcode').value = decodedText;
        lookupBarcode(decodedText);
      }
    },
    function(){}
  ).catch(function(err){
    jslog('Camera error: ' + err, 'ERROR');
    readerDiv.style.display = 'none';
    btn.textContent = getLang()==='lt' ? '📊 Skenuoti kodą' : '📊 Scan Barcode';
    showStatus(getLang()==='lt' ? 'Nepavyko pasiekti kameros.' : 'Could not access camera.', 'warn');
  });
  scannerRunning = true;
}

function stopBarcodeScanner(){
  showStatus('', 'hide');
  var btn = document.getElementById('scanBarcodeBtn');
  btn.textContent = getLang()==='lt' ? '📊 Skenuoti kodą' : '📊 Scan Barcode';

  if(html5QrCode && scannerRunning){
    html5QrCode.stop().then(function(){
      document.getElementById('barcodeReader').style.display = 'none';
      scannerRunning = false;
      jslog('Scanner stopped');
    }).catch(function(e){ scannerRunning = false; });
  } else {
    document.getElementById('barcodeReader').style.display = 'none';
    scannerRunning = false;
  }
}

function showStatus(msg, type){
  if(type === 'hide'){ document.getElementById('scanStatus').className = 'scan-status'; return; }
  var el = document.getElementById('scanStatus');
  var txt = document.getElementById('scanText');
  el.classList.add('active');
  var spinner = el.querySelector('.scan-spinner');
  if(type === 'loading'){
    spinner.style.display = '';
    el.style.background = '';
    el.style.borderColor = '';
    txt.style.color = 'var(--muted)';
  } else if(type === 'success'){
    spinner.style.display = 'none';
    el.style.background = 'rgba(74,222,128,.1)';
    el.style.borderColor = 'rgba(74,222,128,.3)';
    txt.style.color = '#4ade80';
  } else {
    spinner.style.display = 'none';
    el.style.background = 'rgba(245,158,11,.1)';
    el.style.borderColor = 'rgba(245,158,11,.3)';
    txt.style.color = '#f59e0b';
  }
  txt.textContent = msg;
}

function lookupBarcode(code){
  code = (code || '').trim();
  if(!code){
    showStatus('Please enter a barcode number.', 'warn');
    return;
  }
  jslog('Looking up barcode: ' + code);
  showStatus('Looking up barcode ' + code + '...', 'loading');

  fetch('/api/barcode/' + encodeURIComponent(code))
    .then(function(r){ return r.json(); })
    .then(function(data){
      jslog('Barcode result: ' + JSON.stringify(data));
      if(data.found){
        var name = data.brand ? (data.brand + ' ' + data.name) : data.name;
        document.getElementById('pName').value = name;
        document.getElementById('pKcal').value = data.kcal || '';
        document.getElementById('pFat').value = data.fat || '';
        document.getElementById('pProtein').value = data.protein || '';
        document.getElementById('pCarbs').value = data.carbs || '';
        showStatus('Found: ' + name + ' (' + data.kcal + ' kcal, ' + data.fat + 'g fat, ' + data.protein + 'g protein, ' + data.carbs + 'g carbs)', 'success');
        document.getElementById('pName').focus();
      } else {
        showStatus('Product not found in database. Try entering values manually.', 'warn');
      }
    })
    .catch(function(err){
      jslog('Barcode lookup error: ' + err, 'ERROR');
      showStatus('Lookup failed: ' + err.message, 'warn');
    });
}

function parseNum(s){
  return parseFloat(s.replace(',', '.')) || 0;
}
jslog('Barcode scanner JS loaded');
</script>
</div></body></html>"""


HISTORY_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>History — CalorieTracker</title>""" + STYLE + """</head><body>
""" + NAV.replace("active=='history'", "True") + """
<div class="container">
<div class="card">
  <div class="card-title" data-i18n="Daily History (Last 30 Days)">Daily History (Last 30 Days)</div>
  {% if days %}
  <div style="overflow-x:auto">
  <table class="data-table">
    <tr><th data-i18n="Date">Date</th><th data-i18n="Items">Items</th><th data-i18n="Kcal">Kcal</th><th data-i18n="Fat">Fat</th><th data-i18n="Protein">Protein</th><th data-i18n="Carbs">Carbs</th><th></th></tr>
    {% for d in days %}
    <tr>
      <td style="font-weight:500;color:var(--text-strong)">{{ d.log_date }}</td>
      <td>{{ d.items }}</td>
      <td class="kcal-color">{{ d.kcal|int }}{% if goals %} <span style="color:var(--muted);font-size:11px">/ {{ goals.kcal|int }}</span>{% endif %}</td>
      <td class="fat-color">{{ d.fat }}g</td>
      <td class="protein-color">{{ d.protein }}g</td>
      <td class="carbs-color">{{ d.carbs }}g</td>
      <td><a href="/?date={{ d.log_date }}" class="btn-ghost btn-sm" style="display:inline-block;text-decoration:none" data-i18n="View">View</a></td>
    </tr>
    {% endfor %}
  </table>
  </div>
  {% else %}
  <p style="color:var(--muted);text-align:center;padding:2rem" data-i18n="No entries yet. Start logging food on the dashboard.">No entries yet. Start logging food on the dashboard.</p>
  {% endif %}
</div>
</div></body></html>"""

# -- Run --

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=os.environ.get("DEBUG", "0") == "1")
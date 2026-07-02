#!/usr/bin/env python3
"""Calorie Tracker — Flask web app with Google OAuth and macro tracking."""
import os, sys, json, sqlite3, functools
from datetime import datetime, date, timedelta
from flask import Flask, render_template_string, request, g, jsonify, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-abc123")

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
        else:
            cur = db.execute("INSERT INTO users (email, name, picture) VALUES (?,?,?)",
                             (email, idinfo.get("name"), idinfo.get("picture")))
            session["user_id"] = cur.lastrowid
            db.execute("INSERT INTO daily_goals (user_id) VALUES (?)", (cur.lastrowid,))
        db.commit()
        return redirect(url_for("index"))
    except Exception as e:
        print(f"[auth] {e}", file=sys.stderr)
        flash(f"Authentication failed: {e}")
        return redirect(url_for("login"))

@app.route("/auth/dev", methods=["POST"])
def dev_auth():
    """Dev login when no Google OAuth configured."""
    if GOOGLE_CLIENT_ID:
        return redirect(url_for("login"))
    email = request.form.get("email", "dev@localhost")
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if user:
        session["user_id"] = user["id"]
    else:
        cur = db.execute("INSERT INTO users (email, name) VALUES (?,?)", (email, email.split("@")[0]))
        session["user_id"] = cur.lastrowid
        db.execute("INSERT INTO daily_goals (user_id) VALUES (?)", (cur.lastrowid,))
    db.commit()
    return redirect(url_for("index"))

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
        user=user, today=today, products=products,
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
  transition:border-color .2s,box-shadow .2s;
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

NAV = """
<nav class="nav">
  <a href="/" class="nav-brand">
    <div class="nav-brand-icon">🔥</div>
    <span class="nav-brand-name">CalorieTracker</span>
  </a>
  <div class="nav-links">
    <a href="/" class="nav-link {{ 'active' if active=='dashboard' }}">📊 <span class="hide-mobile">Dashboard</span></a>
    <a href="/products" class="nav-link {{ 'active' if active=='products' }}">📦 <span class="hide-mobile">Products</span></a>
    <a href="/history" class="nav-link {{ 'active' if active=='history' }}">📅 <span class="hide-mobile">History</span></a>
    {% if user and user.picture %}<img src="{{ user.picture }}" class="nav-avatar" referrerpolicy="no-referrer">{% endif %}
    <a href="/logout" class="nav-link">↗</a>
  </div>
</nav>
"""

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
         data-login_uri="/auth/google"
         data-auto_prompt="false"></div>
    <div class="g_id_signin" data-type="standard" data-size="large" data-theme="filled_black" data-text="signin_with" data-shape="pill" data-width="300"></div>
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
  <a href="/" style="margin-left:auto">Today</a>
</div>
<script>
(function(){
  var d = new Date('{{ today }}');
  var prev = new Date(d); prev.setDate(prev.getDate()-1);
  var next = new Date(d); next.setDate(next.getDate()+1);
  var fmt = function(dt){ return dt.toISOString().slice(0,10); };
  document.getElementById('prevDay').href = '/?date=' + fmt(prev);
  document.getElementById('nextDay').href = '/?date=' + fmt(next);
  var label = d.toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'});
  if(fmt(d) === fmt(new Date())) label = 'Today — ' + label;
  document.getElementById('dateLabel').textContent = label;
})();
</script>

<!-- DAILY TOTALS -->
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-num kcal-color">{{ totals.kcal }}</div>
    <div class="stat-lbl">kcal{% if goals %} / {{ goals.kcal|int }}{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill kcal-fill" style="width:{{ [totals.kcal/goals.kcal*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num fat-color">{{ totals.fat }}g</div>
    <div class="stat-lbl">fat{% if goals %} / {{ goals.fat|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill fat-fill" style="width:{{ [totals.fat/goals.fat*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num protein-color">{{ totals.protein }}g</div>
    <div class="stat-lbl">protein{% if goals %} / {{ goals.protein|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill protein-fill" style="width:{{ [totals.protein/goals.protein*100, 100]|min }}%"></div></div>{% endif %}
  </div>
  <div class="stat-card">
    <div class="stat-num carbs-color">{{ totals.carbs }}g</div>
    <div class="stat-lbl">carbs{% if goals %} / {{ goals.carbs|int }}g{% endif %}</div>
    {% if goals %}<div class="stat-bar"><div class="stat-fill carbs-fill" style="width:{{ [totals.carbs/goals.carbs*100, 100]|min }}%"></div></div>{% endif %}
  </div>
</div>

<!-- QUICK ADD -->
{% if products %}
<div class="card">
  <div class="card-title">Quick Add</div>
  <div class="quick-add">
    {% for p in products[:12] %}
    <div class="quick-chip" onclick="quickAdd({{ p.id }}, '{{ p.name|e }}')">
      <span class="qname">{{ p.name }}</span>
      <span class="qmeta">{{ p.kcal|int }} kcal/{{ p.per_grams|int }}g</span>
    </div>
    {% endfor %}
  </div>
  <form method="POST" action="/api/log" class="form-row" id="logForm">
    <input type="hidden" name="log_date" value="{{ today }}">
    <div class="form-group wide">
      <label>Product</label>
      <select name="product_id" id="productSelect" required>
        <option value="">Select...</option>
        {% for p in products %}<option value="{{ p.id }}">{{ p.name }} ({{ p.kcal|int }} kcal/{{ p.per_grams|int }}g)</option>{% endfor %}
      </select>
    </div>
    <div class="form-group">
      <label>Grams</label>
      <input name="grams" type="number" step="0.1" min="0" id="gramsInput" required placeholder="100">
    </div>
    <div class="form-group">
      <label>Meal</label>
      <select name="meal">
        <option value="breakfast">Breakfast</option>
        <option value="lunch">Lunch</option>
        <option value="dinner">Dinner</option>
        <option value="snack">Snack</option>
        <option value="other">Other</option>
      </select>
    </div>
    <button type="submit" class="btn">+ Add</button>
  </form>
</div>
{% else %}
<div class="card" style="text-align:center;padding:2rem">
  <p style="color:var(--muted);margin-bottom:1rem">No products yet. Add your first food product to start tracking.</p>
  <a href="/products" class="btn" style="display:inline-block">+ Add Products</a>
</div>
{% endif %}

<!-- TODAY'S LOG -->
{% if entries %}
<div class="card">
  <div class="card-title">Today's Log</div>
  <div style="overflow-x:auto">
  <table class="data-table">
    <tr><th>Food</th><th>Grams</th><th>Meal</th><th>Kcal</th><th>Fat</th><th>Protein</th><th>Carbs</th><th></th></tr>
    {% for e in entries %}
    <tr>
      <td style="font-weight:500;color:var(--text-strong)">{{ e.name }}</td>
      <td>{{ e.grams }}g</td>
      <td><span class="meal-badge meal-{{ e.meal }}">{{ e.meal }}</span></td>
      <td class="kcal-color">{{ e.kcal }}</td>
      <td class="fat-color">{{ e.fat }}g</td>
      <td class="protein-color">{{ e.protein }}g</td>
      <td class="carbs-color">{{ e.carbs }}g</td>
      <td><form method="POST" action="/api/log/{{ e.id }}/delete" style="display:inline"><button type="submit" class="btn-ghost btn-sm" title="Remove">✕</button></form></td>
    </tr>
    {% endfor %}
    <tr style="font-weight:600;border-top:2px solid var(--border-strong)">
      <td colspan="3" style="color:var(--text-strong)">Total</td>
      <td class="kcal-color">{{ totals.kcal }}</td>
      <td class="fat-color">{{ totals.fat }}g</td>
      <td class="protein-color">{{ totals.protein }}g</td>
      <td class="carbs-color">{{ totals.carbs }}g</td>
      <td></td>
    </tr>
  </table>
  </div>
</div>
{% endif %}

<!-- WEEK CHART -->
<div class="card">
  <div class="card-title">7-Day Trend</div>
  <canvas id="weekChart" height="200"></canvas>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
var wdata = {{ week_data|safe }};
new Chart(document.getElementById('weekChart'), {
  type: 'bar',
  data: {
    labels: wdata.map(function(d){ return d.date.slice(5); }),
    datasets: [
      {label:'Kcal', data:wdata.map(function(d){return d.kcal;}), backgroundColor:'rgba(74,222,128,.6)', borderRadius:4, yAxisID:'y'},
      {label:'Protein', data:wdata.map(function(d){return d.protein;}), backgroundColor:'rgba(59,130,246,.6)', borderRadius:4, yAxisID:'y1'},
      {label:'Fat', data:wdata.map(function(d){return d.fat;}), backgroundColor:'rgba(245,158,11,.6)', borderRadius:4, yAxisID:'y1'},
      {label:'Carbs', data:wdata.map(function(d){return d.carbs;}), backgroundColor:'rgba(167,139,250,.6)', borderRadius:4, yAxisID:'y1'}
    ]
  },
  options: {
    responsive:true, interaction:{mode:'index',intersect:false},
    plugins:{legend:{labels:{color:'#8b95a8',font:{size:11}}}},
    scales:{
      x:{ticks:{color:'#5f6776'},grid:{color:'rgba(255,255,255,.04)'}},
      y:{position:'left',ticks:{color:'#4ade80'},grid:{color:'rgba(255,255,255,.04)'},title:{display:true,text:'Kcal',color:'#4ade80'}},
      y1:{position:'right',ticks:{color:'#8b95a8'},grid:{display:false},title:{display:true,text:'Grams',color:'#8b95a8'}}
    }
  }
});
function quickAdd(id, name){
  document.getElementById('productSelect').value = id;
  document.getElementById('gramsInput').focus();
}
</script>

<!-- GOALS -->
<div class="card">
  <div class="card-title">Daily Goals</div>
  <form method="POST" action="/api/goals" class="form-row">
    <div class="form-group"><label>Kcal</label><input name="kcal" type="number" value="{{ goals.kcal|int if goals else 2000 }}"></div>
    <div class="form-group"><label>Fat (g)</label><input name="fat" type="number" value="{{ goals.fat|int if goals else 65 }}"></div>
    <div class="form-group"><label>Protein (g)</label><input name="protein" type="number" value="{{ goals.protein|int if goals else 50 }}"></div>
    <div class="form-group"><label>Carbs (g)</label><input name="carbs" type="number" value="{{ goals.carbs|int if goals else 300 }}"></div>
    <button type="submit" class="btn btn-ghost">Save Goals</button>
  </form>
</div>

</div></body></html>"""

PRODUCTS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Products — CalorieTracker</title>""" + STYLE + """
<style>
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
  <div class="card-title">Add New Product</div>
  <p style="color:var(--muted);font-size:12px;margin-bottom:.75rem">Enter values from the nutrition label, or scan it with your camera.</p>

  <!-- CAMERA SCAN -->
  <script>window.onerror=function(msg,url,line){var d=document.getElementById('ocrDebug');if(d)d.textContent+='JS ERROR: '+msg+' (line '+line+')\\n';return false;};</script>
  <div class="scan-area">
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <label class="scan-btn" style="flex:1;min-width:140px;" id="scanBtnCamera">📷 Take Photo
        <input type="file" accept="image/*" capture="environment" onchange="handleFile(this)" style="display:none" id="scanInputCamera">
      </label>
      <label class="scan-btn" style="flex:1;min-width:140px;" id="scanBtnAlbum">🖼️ From Album
        <input type="file" accept="image/*" onchange="handleFile(this)" style="display:none" id="scanInputAlbum">
      </label>
    </div>
    <div class="scan-preview" id="scanPreview">
      <video id="cameraVideo" autoplay playsinline></video>
      <canvas id="scanCanvas" style="display:none"></canvas>
      <img id="scanImg" style="display:none">
    </div>
    <div class="camera-controls" id="cameraControls">
      <button type="button" class="btn btn-sm" onclick="capturePhoto()">📸 Capture</button>
      <button type="button" class="btn btn-ghost btn-sm" onclick="stopCamera()">Cancel</button>
    </div>
    <div class="scan-actions" id="scanActions" style="display:none">
      <button type="button" class="btn btn-ghost btn-sm" onclick="resetScan()">📷 Try Again</button>
    </div>
    <div class="scan-status" id="scanStatus"><div class="scan-spinner"></div><span id="scanText">Processing...</span></div>
    <pre id="ocrDebug" onclick="this.textContent+='click works! '" style="margin-top:8px;padding:10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;font-size:11px;color:var(--muted);max-height:200px;overflow:auto;white-space:pre-wrap;word-break:break-all">Tap me to test JS...</pre>
    <script>document.getElementById('ocrDebug').textContent='JS works! Page loaded OK.';</script>
  </div>

  <form method="POST" action="/api/products" class="form-row" id="addProductForm">
    <div class="form-group wide"><label>Product Name</label><input name="name" id="pName" required placeholder="e.g. Chicken Breast"></div>
    <div class="form-group"><label>Kcal</label><input name="kcal" id="pKcal" type="number" step="0.1" required placeholder="165"></div>
    <div class="form-group"><label>Fat (g)</label><input name="fat" id="pFat" type="number" step="0.1" placeholder="3.6"></div>
    <div class="form-group"><label>Protein (g)</label><input name="protein" id="pProtein" type="number" step="0.1" placeholder="31"></div>
    <div class="form-group"><label>Carbs (g)</label><input name="carbs" id="pCarbs" type="number" step="0.1" placeholder="0"></div>
    <div class="form-group"><label>Per (g)</label><input name="per_grams" id="pPer" type="number" step="0.1" value="100" placeholder="100"></div>
    <button type="submit" class="btn">+ Add</button>
  </form>
</div>

{% if products %}
<div class="card">
  <div class="card-title">Your Products ({{ products|length }})</div>
  <div style="overflow-x:auto">
  <table class="data-table">
    <tr><th>Name</th><th>Kcal</th><th>Fat</th><th>Protein</th><th>Carbs</th><th>Per</th><th></th></tr>
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
    <div class="card-title">Edit Product</div>
    <form method="POST" id="editForm" class="form-row" style="flex-direction:column;gap:.75rem">
      <div class="form-row">
        <div class="form-group wide"><label>Name</label><input name="name" id="eName" required></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Kcal</label><input name="kcal" id="eKcal" type="number" step="0.1"></div>
        <div class="form-group"><label>Fat</label><input name="fat" id="eFat" type="number" step="0.1"></div>
        <div class="form-group"><label>Protein</label><input name="protein" id="eProtein" type="number" step="0.1"></div>
        <div class="form-group"><label>Carbs</label><input name="carbs" id="eCarbs" type="number" step="0.1"></div>
        <div class="form-group"><label>Per (g)</label><input name="per_grams" id="ePer" type="number" step="0.1"></div>
      </div>
      <div class="form-row">
        <button type="submit" class="btn">Save</button>
        <button type="button" class="btn btn-ghost" onclick="closeEdit()">Cancel</button>
      </div>
    </form>
  </div>
</div>
<script>
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

<!-- Tesseract.js OCR -->
<script>
function jslog(msg, level){
  level = level || 'INFO';
  try {
    var d = document.getElementById('ocrDebug');
    if(d) d.textContent = (d.textContent === 'Debug log will appear here...' ? '' : d.textContent) + '[' + level + '] ' + msg + String.fromCharCode(10);
  } catch(e2){}
  try { fetch('/api/jslog', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({msg:msg, level:level})}); } catch(e){}
}
jslog('Products page JS init');

var cameraStream = null;
var ocrWorker = null;
var tesseractReady = false;

// Resize and preprocess image for better OCR
function resizeForOCR(dataUrl, maxW, callback){
  var img = new Image();
  img.onload = function(){
    var w = img.width;
    var h = img.height;
    var scale = (w > maxW) ? maxW / w : 1;
    var c = document.createElement('canvas');
    c.width = Math.round(w * scale);
    c.height = Math.round(h * scale);
    var ctx = c.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0, c.width, c.height);
    // Convert to grayscale only - let Tesseract handle binarization
    var imageData = ctx.getImageData(0, 0, c.width, c.height);
    var d = imageData.data;
    for(var i = 0; i < d.length; i += 4){
      var gray = Math.round(0.299 * d[i] + 0.587 * d[i+1] + 0.114 * d[i+2]);
      d[i] = gray; d[i+1] = gray; d[i+2] = gray;
    }
    ctx.putImageData(imageData, 0, 0);
    // Also show preview so user can see what OCR sees
    var preview = document.getElementById('ocrPreview');
    if(!preview){
      preview = document.createElement('img');
      preview.id = 'ocrPreview';
      preview.style.cssText = 'max-width:100%;border:2px solid #0af;margin:8px 0;border-radius:8px;';
      var debugEl = document.getElementById('ocrDebug');
      if(debugEl) debugEl.parentNode.insertBefore(preview, debugEl);
    }
    var resultUrl = c.toDataURL('image/png');
    preview.src = resultUrl;
    jslog('Preprocessed image: ' + c.width + 'x' + c.height + ' grayscale');
    callback(resultUrl);
  };
  img.onerror = function(){ callback(dataUrl); };
  img.src = dataUrl;
}

function handleFile(input){
  var file = input.files[0];
  if(!file) return;
  jslog('handleFile called, file: ' + file.name + ' size: ' + file.size + ' type: ' + file.type);
  var status = document.getElementById('scanStatus');
  var statusText = document.getElementById('scanText');
  status.classList.add('active');
  statusText.textContent = 'Loading image...';
  var reader = new FileReader();
  reader.onload = function(ev){
    jslog('FileReader loaded, dataURL length: ' + ev.target.result.length);
    resizeForOCR(ev.target.result, 2000, function(resized){
      jslog('Image resized, new dataURL length: ' + resized.length);
      showImage(resized);
    });
  };
  reader.onerror = function(e){
    jslog('FileReader error: ' + e, 'ERROR');
    statusText.textContent = 'Failed to read image file.';
    statusText.style.color = '#f59e0b';
  };
  reader.readAsDataURL(file);
}

function showImage(src){
  var preview = document.getElementById('scanPreview');
  var img = document.getElementById('scanImg');
  var video = document.getElementById('cameraVideo');
  video.style.display = 'none';
  img.style.display = 'block';
  img.src = src;
  preview.style.display = 'block';
  document.getElementById('cameraControls').classList.remove('active');
  document.getElementById('scanBtnCamera').style.display = 'none';
  document.getElementById('scanBtnAlbum').style.display = 'none';
  // Auto-run OCR immediately
  runOCR();
}

function capturePhoto(){
  var video = document.getElementById('cameraVideo');
  var canvas = document.getElementById('scanCanvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  var dataUrl = canvas.toDataURL('image/jpeg', 0.9);
  stopCamera();
  resizeForOCR(dataUrl, 2000, function(resized){
    showImage(resized);
  });
}

function stopCamera(){
  if(cameraStream){
    cameraStream.getTracks().forEach(function(t){ t.stop(); });
    cameraStream = null;
  }
  document.getElementById('cameraVideo').style.display = 'none';
  document.getElementById('cameraControls').classList.remove('active');
}

function resetScan(){
  stopCamera();
  document.getElementById('scanPreview').style.display = 'none';
  document.getElementById('scanImg').style.display = 'none';
  document.getElementById('scanActions').style.display = 'none';
  document.getElementById('scanStatus').classList.remove('active');
  document.getElementById('scanStatus').style.background = '';
  document.getElementById('scanStatus').style.borderColor = '';
  document.getElementById('ocrDebug').style.display = 'none';
  var spinner = document.getElementById('scanStatus').querySelector('.scan-spinner');
  if(spinner) spinner.style.display = '';
  document.getElementById('scanBtnCamera').style.display = '';
  document.getElementById('scanBtnAlbum').style.display = '';
  document.getElementById('scanInputCamera').value = '';
  document.getElementById('scanInputAlbum').value = '';
}

async function runOCR(){
  var img = document.getElementById('scanImg');
  if(!img.src) return;
  jslog('runOCR called, img.src length: ' + img.src.length);
  var status = document.getElementById('scanStatus');
  var statusText = document.getElementById('scanText');
  status.classList.add('active');
  document.getElementById('scanActions').style.display = 'none';

  // Wait for Tesseract to load (it loads async now)
  if(typeof Tesseract === 'undefined'){
    statusText.textContent = 'Loading OCR library...';
    jslog('Waiting for Tesseract.js to load...');
    var waitCount = 0;
    var waitInterval = setInterval(function(){
      waitCount++;
      if(typeof Tesseract !== 'undefined'){
        clearInterval(waitInterval);
        jslog('Tesseract.js now available after waiting');
        runOCR();
      } else if(waitCount > 30){
        clearInterval(waitInterval);
        jslog('Tesseract.js failed to load after 15s', 'ERROR');
        statusText.textContent = 'OCR library failed to load. Enter values manually.';
        statusText.style.color = '#f59e0b';
        var spinner = status.querySelector('.scan-spinner');
        if(spinner) spinner.style.display = 'none';
        document.getElementById('scanActions').style.display = 'flex';
      }
    }, 500);
    return;
  }

  try {
    statusText.textContent = 'Loading OCR engine (may take 10-20s)...';
    jslog('Creating Tesseract worker...');

    if(!ocrWorker){
      ocrWorker = await Tesseract.createWorker('eng+lit', 1, {
        logger: function(m){
          jslog('Tesseract: ' + m.status + ' ' + Math.round((m.progress||0)*100) + '%');
          if(m.status === 'loading tesseract core'){
            statusText.textContent = 'Loading OCR core...';
          } else if(m.status === 'loading language traineddata'){
            statusText.textContent = 'Loading language data...';
          } else if(m.status === 'initializing tesseract'){
            statusText.textContent = 'Initializing...';
          } else if(m.status === 'recognizing text'){
            statusText.textContent = 'Reading label... ' + Math.round((m.progress||0)*100) + '%';
          }
        }
      });
      jslog('Worker created successfully');
    } else {
      statusText.textContent = 'Reading label...';
      jslog('Reusing existing worker');
    }

    jslog('Calling worker.recognize...');
    var result = await ocrWorker.recognize(img.src);
    var text = result.data.text;
    jslog('OCR raw text: ' + text);
    statusText.textContent = 'Extracting values...';
    parseNutritionLabel(text);
    document.getElementById('scanActions').style.display = 'flex';
  } catch(err) {
    var errMsg = err.message || String(err);
    jslog('OCR error: ' + errMsg, 'ERROR');
    statusText.textContent = 'OCR failed: ' + errMsg;
    statusText.style.color = '#f59e0b';
    document.getElementById('scanActions').style.display = 'flex';
    var spinner = status.querySelector('.scan-spinner');
    if(spinner) spinner.style.display = 'none';
    ocrWorker = null;
  }
}

function parseNutritionLabel(text){
  // Fix common OCR character misreads in numeric contexts
  function fixOcrText(t){
    // Replace common letter-to-digit confusions near numbers/units
    t = t.replace(/([0-9])B/g, '$16');  // 2B0 -> 260
    t = t.replace(/B([0-9])/g, '6$1');  // B0 -> 60
    t = t.replace(/([0-9])O([0-9])/g, '$10$2');  // 1O0 -> 100
    t = t.replace(/([0-9])l([0-9])/g, '$11$2');  // 3l1 -> 311
    t = t.replace(/([0-9])I([0-9])/g, '$11$2');  // 3I1 -> 311
    t = t.replace(/([0-9])S([0-9])/g, '$15$2');  // 2S0 -> 250
    // Fix spaces that break decimal numbers: "3 1" near "g" -> "3.1"
    t = t.replace(/(\d+)\s+(\d)\s*g/gi, '$1.$2 g');
    // Fix "319" that should be "3.19" or "3,1" (3+ digits with no decimal before 'g')
    return t;
  }

  var fixedText = fixOcrText(text);
  jslog('OCR after fixes: ' + fixedText.substring(0, 500));
  
  // Normalize: fix common OCR errors, normalize whitespace
  var allText = fixedText.replace(/[|]/g, ' ').replace(/\s+/g, ' ');
  // Also try line-by-line for structured labels
  var lines = fixedText.split(String.fromCharCode(10)).map(function(l){ return l.trim(); }).filter(function(l){ return l; });

  // Helper: find number near a keyword in any line
  function findValue(keywords, isEnergy){
    for(var i=0; i<lines.length; i++){
      var line = lines[i];
      for(var k=0; k<keywords.length; k++){
        if(line.toLowerCase().indexOf(keywords[k]) >= 0){
          var nums = line.match(/(\d+[\.,]\d+|\d+)/g);
          if(nums && nums.length > 0){
            if(isEnergy){
              var parsed = nums.map(function(n){ return parseFloat(n.replace(',','.')); }).filter(function(v){ return v > 0 && !isNaN(v); });
              jslog('Energy line: ' + line + ' -> parsed nums: ' + JSON.stringify(parsed));
              if(parsed.length >= 2){
                parsed.sort(function(a,b){ return a - b; });
                // kcal is the smaller value (1 kcal = 4.184 kJ)
                // But validate: if smallest < 1, skip it
                var kcal = parsed[0];
                if(kcal < 1 && parsed.length > 1) kcal = parsed[1];
                return Math.round(kcal);
              }
              if(parsed.length === 1){
                // Single number - if > 400 it might be kJ, convert
                if(parsed[0] > 400) return Math.round(parsed[0] / 4.184);
                return Math.round(parsed[0]);
              }
            }
            // For macros, take the first number after the keyword
            var afterKeyword = line.substring(line.toLowerCase().indexOf(keywords[k]) + keywords[k].length);
            var afterNum = afterKeyword.match(/(\d+[\.,]\d+|\d+)/);
            if(afterNum){
              var val = parseFloat(afterNum[1].replace(',','.'));
              // Sanity: macros per 100g should be 0-100
              if(val > 100){
                jslog('Suspicious value ' + val + ' for ' + keywords[k] + ', trying /10: ' + (val/10));
                val = Math.round(val/10 * 10) / 10;
              }
              return val;
            }
            return parseFloat(nums[0].replace(',','.'));
          }
        }
      }
    }
    // Fallback: search in full text
    for(var k=0; k<keywords.length; k++){
      var re = new RegExp(keywords[k] + '[^\\d]{0,20}(\\d+[\\.,]?\\d*)', 'i');
      var m = allText.match(re);
      if(m){
        var val = parseFloat(m[1].replace(',','.'));
        if(!isEnergy && val > 100) val = Math.round(val/10 * 10) / 10;
        return val;
      }
    }
    return null;
  }

  // Energy / Calories
  var kcalVal = findValue(['kcal', 'kkal', 'energi', 'energy', 'calories', 'kalorij', 'energin', 'energ'], true);

  // Fat
  var fatVal = findValue(['fat', 'riebal', 'fedt', 'fett', 'lipid', 'grassi', 'grasa', 'vet', 'tuk'], false);

  // Protein
  var proteinVal = findValue(['protein', 'baltym', 'protei', 'eiwit', 'blanc', 'belok'], false);

  // Carbs
  var carbsVal = findValue(['carbohydrate', 'angliavandeniai', 'angliavandeni', 'kolhydrat', 'carboidrat', 'kohlenhydrat', 'carb', 'hidrat', 'glucid', 'sacharid', 'koolhydra'], false);

  // Fill form
  jslog('Extracted values - kcal:' + kcalVal + ' fat:' + fatVal + ' protein:' + proteinVal + ' carbs:' + carbsVal);
  var filled = [];
  if(kcalVal !== null && kcalVal > 0){
    document.getElementById('pKcal').value = kcalVal;
    filled.push('kcal: ' + kcalVal);
  }
  if(fatVal !== null){
    document.getElementById('pFat').value = fatVal;
    filled.push('fat: ' + fatVal + 'g');
  }
  if(proteinVal !== null){
    document.getElementById('pProtein').value = proteinVal;
    filled.push('protein: ' + proteinVal + 'g');
  }
  if(carbsVal !== null){
    document.getElementById('pCarbs').value = carbsVal;
    filled.push('carbs: ' + carbsVal + 'g');
  }

  // Show result
  var statusEl = document.getElementById('scanStatus');
  var statusText = document.getElementById('scanText');
  if(filled.length > 0){
    statusEl.classList.add('active');
    statusEl.style.background = 'rgba(74,222,128,.1)';
    statusEl.style.borderColor = 'rgba(74,222,128,.3)';
    statusText.textContent = 'Found: ' + filled.join(', ') + '. Review and add product name.';
    statusText.style.color = '#4ade80';
    document.getElementById('pName').focus();
  } else {
    statusEl.classList.add('active');
    statusEl.style.background = 'rgba(245,158,11,.1)';
    statusText.textContent = 'Could not extract values automatically. Please enter manually.';
    statusText.style.color = '#f59e0b';
  }
  // Show scan actions for retry
  document.getElementById('scanActions').style.display = 'flex';
  // Remove spinner
  var spinner = statusEl.querySelector('.scan-spinner');
  if(spinner) spinner.style.display = 'none';
}

function parseNum(s){
  return parseFloat(s.replace(',', '.')) || 0;
}

jslog('All JS functions defined OK');
</script>
<script>
// Load Tesseract AFTER all functions are defined so handleFile always exists
var tScript = document.createElement('script');
tScript.src = 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js';
tScript.onload = function(){
  tesseractReady = true;
  jslog('Tesseract.js loaded and ready');
};
tScript.onerror = function(){
  jslog('Tesseract.js FAILED to load from CDN', 'ERROR');
};
document.head.appendChild(tScript);
jslog('Tesseract.js loading started (async)');
</script>
</div></body></html>"""

# -- Run --

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=os.environ.get("DEBUG", "0") == "1")

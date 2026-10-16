"""CipherChat — real-time end-to-end encrypted chat rooms. Messages are encrypted in the browser with AES-256-GCM
using a key derived (PBKDF2, 150k iterations) from a room passphrase that never leaves your device.
The server only ever stores ciphertext. Includes presence, typing indicators and self-destructing messages.
Run:  python app.py   →  http://127.0.0.1:5000   (demo: demo@example.com / Demo@1234, room passphrase: demo-secret)
"""
import base64
import os
import random
import secrets
import string
import time
from datetime import datetime, timedelta

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from core import create_app, execute, login_required, now, query

UI = dict(
    name="CipherChat", short="End-to-End Encrypted Chat", icon="🔐", accent1="#3B82F6", accent2="#14F195", home="rooms",
    tagline="Private group chat where even the server can't read your messages. AES-256-GCM in your browser, live presence and self-destructing messages.",
    hero='Talk freely. <span class="gradient-text">Zero-knowledge by design.</span>', badge="Privacy-first messaging",
    features=[("bi-shield-lock", "End-to-end encryption", "AES-256-GCM + PBKDF2 — keys never leave your device"),
              ("bi-lightning", "Real-time", "Live messages, online presence and typing indicators"),
              ("bi-hourglass-split", "Self-destruct", "Messages that vanish after 1 min, 1 hour or 1 day")],
    float=['🔒 <code>U2FsdGVkX1+8vJ…</code> ← what the server sees', '💬 <b>Priya</b> is typing<span class="gradient-text">…</span>'],
    nav=[("Chat Rooms", "rooms", "bi-chat-square-dots"), ("Security", "security", "bi-shield-check")],
    search_hint="Search rooms…", promo_title="Zero knowledge", promo="Forget the passphrase and the messages are unrecoverable — by design.",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    salt TEXT NOT NULL, verifier_iv TEXT, verifier_ct TEXT, default_ttl INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined TEXT, last_seen REAL DEFAULT 0, typing_until REAL DEFAULT 0, PRIMARY KEY(room_id, user_id));
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, iv TEXT NOT NULL, ct TEXT NOT NULL, created_at TEXT NOT NULL, expires_at REAL);
"""
B64 = lambda b: base64.b64encode(b).decode()


def server_encrypt(passphrase, salt_b64, text):
    """Only used to seed the demo room — mirrors the browser's Web Crypto scheme exactly."""
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), base64.b64decode(salt_b64), 150_000, 32)
    iv = os.urandom(12)
    return B64(iv), B64(AESGCM(key).encrypt(iv, text.encode(), None))


def seed_demo(uid):
    from werkzeug.security import generate_password_hash
    salt = B64(os.urandom(16))
    try:
        viv, vct = server_encrypt("demo-secret", salt, "cipherchat-ok")
    except ImportError:  # cryptography not installed → skip demo room
        return
    rid = execute("INSERT INTO rooms(code,name,owner_id,salt,verifier_iv,verifier_ct,created_at) VALUES(?,?,?,?,?,?,?)",
                  ("DEMO42", "Project Alpha 🚀", uid, salt, viv, vct, now()))
    friends = []
    for name, email, col in [("Priya Sharma", "priya.demo@example.com", "#FF416C"), ("Arjun Kumar", "arjun.demo@example.com", "#F7B32B")]:
        fid = execute("INSERT INTO users(name,email,password,avatar_color) VALUES(?,?,?,?)", (name, email, generate_password_hash(secrets.token_hex(16)), col))
        friends.append(fid)
    for u in [uid] + friends:
        execute("INSERT INTO members(room_id,user_id,joined,last_seen) VALUES(?,?,?,?)", (rid, u, now(), time.time() - 3600))
    convo = [(friends[0], "Hey team! 👋 Is everyone on the encrypted channel?"), (uid, "Yes! Server only stores ciphertext now 🔐"),
             (friends[1], "Nice. I'll share the API keys here instead of email."), (friends[0], "Deploy is scheduled for Friday 6 PM."),
             (uid, "Perfect — I'll run the final security review on Thursday."), (friends[1], "Remember to enable self-destruct for passwords ⏳"),
             (friends[0], "Toggle 'Show what the server sees' to look at the raw ciphertext 😎")]
    base = datetime.now() - timedelta(minutes=40)
    for i, (u, text) in enumerate(convo):
        iv, ct = server_encrypt("demo-secret", salt, text)
        execute("INSERT INTO messages(room_id,user_id,iv,ct,created_at) VALUES(?,?,?,?,?)", (rid, u, iv, ct, (base + timedelta(minutes=i * 5)).strftime("%Y-%m-%d %H:%M:%S")))


app = create_app(__name__, UI, SCHEMA, seed_demo=seed_demo)


def room_or_404(code, member=True):
    r = query("SELECT * FROM rooms WHERE code=?", (code.upper(),), one=True)
    if not r:
        abort(404)
    if member and not query("SELECT 1 FROM members WHERE room_id=? AND user_id=?", (r["id"], g.user["id"]), one=True):
        abort(403)
    return r


@app.route("/rooms")
@login_required
def rooms():
    rows = query("SELECT r.*, (SELECT COUNT(*) FROM members m WHERE m.room_id=r.id) n_members, (SELECT COUNT(*) FROM messages x WHERE x.room_id=r.id) n_msgs, "
                 "(SELECT MAX(created_at) FROM messages x WHERE x.room_id=r.id) last_msg, "
                 "(SELECT COUNT(*) FROM members m WHERE m.room_id=r.id AND m.last_seen > ?) online "
                 "FROM rooms r JOIN members me ON me.room_id=r.id AND me.user_id=? ORDER BY last_msg DESC", (time.time() - 12, g.user["id"]))
    return render_template("rooms.html", rows=rows, page_title="Chat Rooms")


@app.post("/api/rooms")
@login_required
def api_create_room():
    d = request.get_json(force=True)
    name = str(d.get("name", "")).strip()[:60]
    if len(name) < 2 or not d.get("salt") or not d.get("verifier_ct"):
        return jsonify(error="Room name and passphrase are required"), 400
    while True:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if not query("SELECT 1 FROM rooms WHERE code=?", (code,), one=True):
            break
    rid = execute("INSERT INTO rooms(code,name,owner_id,salt,verifier_iv,verifier_ct,default_ttl,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (code, name, g.user["id"], d["salt"], d["verifier_iv"], d["verifier_ct"], int(d.get("ttl", 0)), now()))
    execute("INSERT INTO members(room_id,user_id,joined,last_seen) VALUES(?,?,?,?)", (rid, g.user["id"], now(), time.time()))
    return jsonify(code=code, url=url_for("room", code=code))


@app.post("/join")
@login_required
def join():
    code = request.form.get("code", "").strip().upper()
    r = query("SELECT * FROM rooms WHERE code=?", (code,), one=True)
    if not r:
        flash("No room with that code.", "danger")
        return redirect(url_for("rooms"))
    execute("INSERT OR IGNORE INTO members(room_id,user_id,joined,last_seen) VALUES(?,?,?,?)", (r["id"], g.user["id"], now(), time.time()))
    return redirect(url_for("room", code=code))


@app.route("/room/<code>")
@login_required
def room(code):
    r = room_or_404(code)
    return render_template("room.html", r=r, page_title=r["name"])


@app.post("/room/<code>/leave")
@login_required
def leave(code):
    r = room_or_404(code)
    execute("DELETE FROM members WHERE room_id=? AND user_id=?", (r["id"], g.user["id"]))
    flash("You left the room.", "info")
    return redirect(url_for("rooms"))


@app.route("/security")
@login_required
def security():
    return render_template("security.html", page_title="How the encryption works")


# ---------------------------------------------------------------- realtime API (short polling)
@app.get("/api/room/<code>/sync")
@login_required
def api_sync(code):
    r = room_or_404(code)
    t = time.time()
    execute("DELETE FROM messages WHERE room_id=? AND expires_at IS NOT NULL AND expires_at < ?", (r["id"], t))
    execute("UPDATE members SET last_seen=? WHERE room_id=? AND user_id=?", (t, r["id"], g.user["id"]))
    after = request.args.get("after", 0, type=int)
    msgs = query("SELECT m.id, m.user_id, m.iv, m.ct, m.created_at, m.expires_at, u.name, u.avatar_color FROM messages m JOIN users u ON u.id=m.user_id "
                 "WHERE m.room_id=? AND m.id>? ORDER BY m.id LIMIT 200", (r["id"], after))
    live_ids = [x["id"] for x in query("SELECT id FROM messages WHERE room_id=?", (r["id"],))]
    members = query("SELECT u.id, u.name, u.avatar_color, m.last_seen, m.typing_until FROM members m JOIN users u ON u.id=m.user_id WHERE m.room_id=? ORDER BY u.name", (r["id"],))
    for m in members:
        m["online"] = t - m["last_seen"] < 12
        m["typing"] = m["typing_until"] > t and m["id"] != g.user["id"]
        m.pop("last_seen"), m.pop("typing_until")
    return jsonify(messages=msgs, members=members, live_ids=live_ids, now=t)


@app.post("/api/room/<code>/messages")
@login_required
def api_send(code):
    r = room_or_404(code)
    d = request.get_json(force=True)
    iv, ct = str(d.get("iv", "")), str(d.get("ct", ""))
    if not (12 <= len(iv) <= 32) or not (8 <= len(ct) <= 20000):
        return jsonify(error="Invalid encrypted payload"), 400
    ttl = int(d.get("ttl", 0))
    mid = execute("INSERT INTO messages(room_id,user_id,iv,ct,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                  (r["id"], g.user["id"], iv, ct, now(), time.time() + ttl if ttl > 0 else None))
    execute("UPDATE members SET typing_until=0 WHERE room_id=? AND user_id=?", (r["id"], g.user["id"]))
    return jsonify(id=mid)


@app.post("/api/room/<code>/typing")
@login_required
def api_typing(code):
    r = room_or_404(code)
    execute("UPDATE members SET typing_until=? WHERE room_id=? AND user_id=?", (time.time() + 4, r["id"], g.user["id"]))
    return jsonify(ok=True)


@app.delete("/api/room/<code>/messages/<int:mid>")
@login_required
def api_delete_msg(code, mid):
    r = room_or_404(code)
    execute("DELETE FROM messages WHERE id=? AND room_id=? AND user_id=?", (mid, r["id"], g.user["id"]))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, threaded=True)

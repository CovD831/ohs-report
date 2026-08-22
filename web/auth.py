"""用户认证 + 审计日志 — 多人使用的基础设施

users: 用户表 (用户名+pbkdf2密码哈希, admin两档)
audit_log: 全请求审计 (谁/何时/做了什么/结果/耗时)
session: 内存 session (签名cookie)
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"
SECRET_FILE = Path(__file__).resolve().parent.parent / "data" / ".secret"


def _secret() -> bytes:
    """会话签名密钥 (首次生成, 持久化)"""
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().encode()


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


# ===== users =====
def init_users():
    conn = sqlite3.connect(str(DB))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw_hash TEXT, salt TEXT,
      is_admin INTEGER DEFAULT 0, created TEXT DEFAULT (datetime('now','localtime')));
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now','localtime')),
      user TEXT, method TEXT, path TEXT, status INTEGER,
      ip TEXT, duration_ms INTEGER, detail TEXT);
    """)
    # 首个管理员 (环境变量或默认, 首次创建后可改密)
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
        salt = secrets.token_hex(8)
        conn.execute("INSERT INTO users(username,pw_hash,salt,is_admin) VALUES(?,?,?,1)",
                     ("admin", _hash_pw(admin_pw, salt), salt))
    conn.commit()
    conn.close()


def verify_user(username: str, password: str) -> dict | None:
    conn = sqlite3.connect(str(DB))
    r = conn.execute("SELECT username, pw_hash, salt, is_admin FROM users WHERE username=?",
                     (username,)).fetchone()
    conn.close()
    if not r:
        return None
    if hmac.compare_digest(_hash_pw(password, r[2]), r[1]):
        return {"username": r[0], "is_admin": bool(r[3])}
    return None


def create_user(username: str, password: str, is_admin: bool = False) -> bool:
    conn = sqlite3.connect(str(DB))
    try:
        salt = secrets.token_hex(8)
        conn.execute("INSERT INTO users(username,pw_hash,salt,is_admin) VALUES(?,?,?,?)",
                     (username, _hash_pw(password, salt), salt, int(is_admin)))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = sqlite3.connect(str(DB))
    rows = conn.execute("SELECT username, is_admin, created FROM users ORDER BY id").fetchall()
    conn.close()
    return [{"username": r[0], "is_admin": bool(r[1]), "created": r[2]} for r in rows]


def delete_user(username: str) -> bool:
    if username == "admin":
        return False
    conn = sqlite3.connect(str(DB))
    n = conn.execute("DELETE FROM users WHERE username=?", (username,)).rowcount
    conn.commit()
    conn.close()
    return n > 0


def change_password(username: str, new_password: str) -> bool:
    salt = secrets.token_hex(8)
    conn = sqlite3.connect(str(DB))
    n = conn.execute("UPDATE users SET pw_hash=?, salt=? WHERE username=?",
                     (_hash_pw(new_password, salt), salt, username)).rowcount
    conn.commit()
    conn.close()
    return n > 0


# ===== session (签名 token: username.expiry.hmac) =====
def make_token(username: str, days: int = 7) -> str:
    exp = str(int(time.time()) + days * 86400)
    msg = f"{username}.{exp}"
    sig = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def parse_token(token: str) -> dict | None:
    try:
        username, exp, sig = token.split(".")
        msg = f"{username}.{exp}"
        want = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, want):
            return None
        if time.time() > int(exp):
            return None
        conn = sqlite3.connect(str(DB))
        r = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if not r:
            return None
        return {"username": username, "is_admin": bool(r[0])}
    except Exception:
        return None


# ===== audit =====
def audit(user: str, method: str, path: str, status: int, ip: str, dur_ms: int,
          detail: str = ""):
    try:
        conn = sqlite3.connect(str(DB))
        conn.execute("INSERT INTO audit_log(user,method,path,status,ip,duration_ms,detail) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (user or "-", method, path[:120], status, ip or "-", dur_ms, detail[:500]))
        conn.commit()
        conn.close()
    except Exception:
        pass  # 审计失败不阻塞主流程


def query_audit(limit: int = 200, user: str | None = None,
                action: str | None = None) -> list[dict]:
    conn = sqlite3.connect(str(DB))
    sql = "SELECT ts,user,method,path,status,ip,duration_ms,detail FROM audit_log WHERE 1=1"
    args: list = []
    if user:
        sql += " AND user=?"
        args.append(user)
    if action:
        sql += " AND path LIKE ?"
        args.append(f"%{action}%")
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 1000))
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    keys = ["ts", "user", "method", "path", "status", "ip", "duration_ms", "detail"]
    return [dict(zip(keys, r)) for r in rows]

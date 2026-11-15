"""Audit log of sensitive administrative operations: who did what to which account or
resource, and when.

Only labels and changed setting names/values go into ``detail``; passwords, API keys,
prompts and file contents never do (``_SECRET_WORDS`` strips anything that looks like one,
as a second line of defence behind the callers).
"""

from __future__ import annotations

import json
import time

from . import db

_SECRET_WORDS = ("password", "api_key", "apikey", "token", "secret", "prompt", "credential")
MAX_DETAIL = 2000


def _clean(detail: dict | None) -> dict:
    out = {}
    for k, v in (detail or {}).items():
        if any(w in str(k).lower() for w in _SECRET_WORDS):
            continue
        out[str(k)[:60]] = v if isinstance(v, (int, float, bool)) or v is None else str(v)[:300]
    return out


def record(actor: dict | None, action: str, target_type: str = "", target_id: object = "", target_label: str = "",
           detail: dict | None = None) -> None:
    text = json.dumps(_clean(detail))[:MAX_DETAIL]
    with db.tx() as c:
        c.execute("INSERT INTO audit_log (actor_id, actor_label, action, target_type, target_id, target_label, detail, at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (actor["id"] if actor else None, (actor or {}).get("email", "system"), action, target_type,
                   str(target_id), target_label[:200], text, time.time()))


def entries(limit: int = 100, before_id: int | None = None, target_type: str | None = None,
            target_id: str | None = None, q: str = "") -> list[dict]:
    sql = "SELECT * FROM audit_log WHERE 1 = 1"
    args: list = []
    if before_id:
        sql += " AND id < ?"
        args.append(before_id)
    if target_type:
        sql += " AND target_type = ?"
        args.append(target_type)
    if target_id:
        sql += " AND target_id = ?"
        args.append(str(target_id))
    if q:
        sql += " AND (action LIKE ? OR actor_label LIKE ? OR target_label LIKE ?)"
        like = f"%{q.strip()[:80]}%"
        args += [like, like, like]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with db.tx() as c:
        rows = c.execute(sql, args).fetchall()
    return [{"id": r["id"], "actor": r["actor_label"], "action": r["action"], "target_type": r["target_type"],
             "target_id": r["target_id"], "target": r["target_label"], "detail": json.loads(r["detail"] or "{}"),
             "at": r["at"]} for r in rows]


def purge(older_than_days: int) -> int:
    with db.tx() as c:
        return c.execute("DELETE FROM audit_log WHERE at < ?", (time.time() - older_than_days * 86400,)).rowcount

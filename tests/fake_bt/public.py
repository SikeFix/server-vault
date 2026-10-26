"""测试用的宝塔 public 模块替身:实现 server-vault 用到的 M() / md5 / password_salt / GetRandomString。"""
import hashlib
import json
import os
import random
import string

_DB = os.path.join(os.environ.get("BT_PANEL", "."), "data", "fake_db.json")


def _load():
    try:
        with open(_DB) as f:
            return json.load(f)
    except Exception:
        return {"users": [{"id": 1, "username": "bt_admin", "password": ""}], "temp_login": []}


def _save(d):
    with open(_DB, "w") as f:
        json.dump(d, f)


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def password_salt(password, username=None, uid=None):
    return md5(password + "|salt|" + str(uid))


def GetRandomString(n):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


class _Query:
    def __init__(self, table):
        self.table, self.cond = table, None

    def where(self, cond, args):
        self.cond = (cond, args)
        return self

    def _match(self, r):
        if not self.cond:
            return True
        cond, args = self.cond
        if cond == "id=?":
            return r["id"] == args[0]
        if cond.startswith("state=?"):
            return r["state"] == args[0] and r["expire"] > args[1]
        return False

    def getField(self, field):
        rows = [r for r in _load()[self.table] if self._match(r)]
        return rows[0][field] if rows else None

    def setField(self, field, value):
        d = _load()
        for r in d[self.table]:
            if self._match(r):
                r[field] = value
        _save(d)
        return 1

    def delete(self):
        d = _load()
        d[self.table] = [r for r in d[self.table] if not self._match(r)]
        _save(d)

    def count(self):
        return len(_load()[self.table])

    def insert(self, row):
        d = _load()
        row.setdefault("id", 101 + len(d[self.table]))
        d[self.table].append(row)
        _save(d)
        return row["id"]


def M(table):
    return _Query(table)

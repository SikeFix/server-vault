"""主密码保险库:scrypt 派生密钥 + AES-256-GCM 加密所有凭据,密钥只保存在内存中。"""
import base64
import os
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .db import db

VERIFIER = b"server-vault:v1"
ENC_COLUMNS = {
    "servers": ["password_enc", "private_key_enc", "key_passphrase_enc", "pending_password_enc", "pending_key_enc"],
    "password_history": ["password_enc"],
}


class VaultError(Exception):
    pass


class VaultLocked(VaultError):
    pass


def _derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())


def _enc(key: bytes, data: bytes) -> str:
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, data, None)).decode()


def _dec(key: bytes, blob: str) -> bytes:
    raw = base64.b64decode(blob)
    return AESGCM(key).decrypt(raw[:12], raw[12:], None)


class Vault:
    def __init__(self):
        self._key = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return db.get_meta("kdf_salt") is not None

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def setup(self, password: str):
        with self._lock:
            if self.initialized:
                raise VaultError("保险库已初始化")
            if len(password) < 8:
                raise VaultError("主密码至少 8 位")
            salt = os.urandom(16)
            key = _derive(password, salt)
            db.set_meta("kdf_salt", salt.hex())
            db.set_meta("verifier", _enc(key, VERIFIER))
            self._key = key

    def _check(self, password: str):
        salt = db.get_meta("kdf_salt")
        if not salt:
            return None
        key = _derive(password, bytes.fromhex(salt))
        try:
            return key if _dec(key, db.get_meta("verifier")) == VERIFIER else None
        except (InvalidTag, ValueError):
            return None

    def unlock(self, password: str) -> bool:
        key = self._check(password)
        if key:
            self._key = key
        return key is not None

    def verify(self, password: str) -> bool:
        return self._check(password) is not None

    def lock(self):
        self._key = None

    def encrypt(self, text):
        if text is None or text == "":
            return None
        if not self._key:
            raise VaultLocked("保险库已锁定")
        return _enc(self._key, text.encode())

    def decrypt(self, blob):
        if not blob:
            return None
        if not self._key:
            raise VaultLocked("保险库已锁定")
        return _dec(self._key, blob).decode()

    def change_password(self, old: str, new: str):
        if len(new) < 8:
            raise VaultError("新主密码至少 8 位")
        old_key = self._check(old)
        if not old_key:
            raise VaultError("原主密码错误")
        salt = os.urandom(16)
        new_key = _derive(new, salt)
        with db.tx():
            for table, cols in ENC_COLUMNS.items():
                for row in db.all(f"SELECT id,{','.join(cols)} FROM {table}"):
                    values = [_enc(new_key, _dec(old_key, row[c])) if row[c] else None for c in cols]
                    sets = ",".join(f"{c}=?" for c in cols)
                    db.run(f"UPDATE {table} SET {sets} WHERE id=?", (*values, row["id"]))
            db.set_meta("kdf_salt", salt.hex())
            db.set_meta("verifier", _enc(new_key, VERIFIER))
        self._key = new_key


vault = Vault()

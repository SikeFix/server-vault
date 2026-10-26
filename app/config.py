import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SV_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "vault.db"
STATIC_DIR = BASE_DIR / "static"

HOST = os.environ.get("SV_HOST", "127.0.0.1")
PORT = int(os.environ.get("SV_PORT", "8780"))

# 可选:无人值守时用环境变量自动解锁保险库(让 Agent 在服务重启后无需人工解锁)
AUTO_UNLOCK_PASSWORD = os.environ.get("SV_MASTER_PASSWORD")

VERSION = "1.5.0"

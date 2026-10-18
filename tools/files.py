"""文件读取"""
import os
import re
from core.constants import (ENABLE_FILE_READ, FILE_READ_KEYWORDS,
    SUPPORTED_EXTENSIONS, FILE_SEARCH_DIRS, MAX_FILE_SIZE)


def should_read(text):
    if not ENABLE_FILE_READ or not text: return False
    tl = text.lower()
    if not any(kw in text for kw in FILE_READ_KEYWORDS): return False
    return any(ext in tl for ext in SUPPORTED_EXTENSIONS)


def extract_name(text):
    ep = "|".join([re.escape(e.lstrip(".")) for e in SUPPORTED_EXTENSIONS])
    m = re.findall(rf'([\w\u4e00-\u9fa5\-\./\\]+\.(?:{ep}))', text, re.IGNORECASE)
    return max(m, key=len) if m else None


def find_file(fn):
    if not fn: return None
    if os.path.isabs(fn) and os.path.exists(fn): return fn
    for sd in FILE_SEARCH_DIRS:
        if not os.path.exists(sd): continue
        c = os.path.join(sd, fn)
        if os.path.exists(c): return c
        for root, dirs, files in os.walk(sd):
            dirs[:] = [d for d in dirs if d not in
                ["node_modules", ".git", "__pycache__", "venv", ".venv",
                 "dist", "build", ".next"]]
            for f in files:
                if f.lower() == fn.lower(): return os.path.join(root, f)
    return None


def read_content(fp):
    try:
        sz = os.path.getsize(fp)
        if sz > MAX_FILE_SIZE * 3: return f"[文件过大 {sz} 字节]"
        try:
            with open(fp, "r", encoding="utf-8") as f: return f.read(MAX_FILE_SIZE)
        except UnicodeDecodeError:
            with open(fp, "r", encoding="gbk", errors="replace") as f: return f.read(MAX_FILE_SIZE)
    except Exception as e: return f"[读取失败] {e}"
"""日志 + 崩溃报告 + 运行标记"""
import os
import glob
import time
import traceback
import logging
from logging.handlers import RotatingFileHandler
from core.constants import LOG_DIR

logger = logging.getLogger("feimu")
logger.setLevel(logging.INFO)
logger.handlers.clear()
_lf = os.path.join(LOG_DIR, f"feimu_{time.strftime('%Y-%m-%d')}.log")
try:
    _fh = RotatingFileHandler(_lf, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    _fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(_fh)
except: pass
try:
    _ch = logging.StreamHandler()
    _ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
    logger.addHandler(_ch)
except: pass

RUNNING_FLAG = os.path.join(os.path.dirname(LOG_DIR), ".feimu_running")


def cleanup_old_crash_reports(keep=10):
    try:
        cl = glob.glob(os.path.join(LOG_DIR, "crash_*.txt"))
        if len(cl) <= keep: return
        cl.sort(key=os.path.getmtime)
        for f in cl[:-keep]:
            try: os.remove(f)
            except: pass
    except: pass


def save_crash_report(exc_info):
    try:
        et, ev, _ = exc_info
        fp = f"{et.__name__}: {ev}"
        for old in sorted(glob.glob(os.path.join(LOG_DIR, "crash_*.txt")),
                          key=os.path.getmtime, reverse=True)[:5]:
            try:
                with open(old, "r", encoding="utf-8") as f: c = f.read()
                if fp in c:
                    cnt = c.count("【重复次数】")
                    with open(old, "a", encoding="utf-8") as f:
                        f.write(f"\n【重复次数】{cnt+2} 时间：{time.strftime('%H:%M:%S')}\n")
                    return old
            except: pass
        cf = os.path.join(LOG_DIR, f"crash_{time.strftime('%Y-%m-%d_%H-%M-%S')}.txt")
        with open(cf, "w", encoding="utf-8") as f:
            f.write(f"崩溃：{time.strftime('%Y-%m-%d %H:%M:%S')}\n指纹：{fp}\n{'='*60}\n")
            traceback.print_exception(*exc_info, file=f)
        cleanup_old_crash_reports(keep=10)
        return cf
    except: return None


def check_last_crash():
    if os.path.exists(RUNNING_FLAG):
        cl = glob.glob(os.path.join(LOG_DIR, "crash_*.txt"))
        if cl: return max(cl, key=os.path.getmtime)
    return None


def mark_running():
    try:
        with open(RUNNING_FLAG, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n{time.time()}\n")
    except: pass


def mark_clean_exit():
    try:
        if os.path.exists(RUNNING_FLAG): os.remove(RUNNING_FLAG)
    except: pass
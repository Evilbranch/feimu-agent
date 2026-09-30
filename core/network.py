"""断网检测"""
import time
import requests

_URLS = ["https://www.baidu.com", "https://api.deepseek.com"]
_s = {"online": True, "last_check": 0, "check_interval": 30, "fail": 0, "warned": False}


def is_online(force_check=False):
    now = time.time()
    if not force_check and (now - _s["last_check"]) < _s["check_interval"]:
        return _s["online"]
    _s["last_check"] = now
    for url in _URLS:
        try:
            r = requests.head(url, timeout=2, allow_redirects=True)
            if r.status_code < 500:
                if not _s["online"]: print(f"\n🌐 网络已恢复")
                _s["online"] = True; _s["fail"] = 0; _s["warned"] = False
                return True
        except: continue
    _s["fail"] += 1
    if _s["fail"] >= 2:
        _s["online"] = False
        if not _s["warned"]:
            print(f"\n⚠️ 检测到断网，切到离线模式"); _s["warned"] = True
    return _s["online"]
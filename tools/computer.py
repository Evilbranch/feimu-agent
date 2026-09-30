"""电脑控制 - 打开 + 关闭"""
import os
import json
import subprocess
from core.constants import LEARNED_APPS_FILE, MY_APPS_DIR


APP_ALIAS = {
    "微信": ["微信", "wechat", "weixin"], "qq": ["qq", "腾讯qq"],
    "网易云": ["网易云", "cloudmusic", "网易云音乐"],
    "浏览器": ["chrome", "edge", "firefox", "浏览器"],
    "chrome": ["chrome", "谷歌浏览器"], "edge": ["edge", "微软浏览器"],
    "记事本": ["记事本", "notepad"], "计算器": ["计算器", "calculator", "calc"],
    "文件管理器": ["文件资源管理器", "文件管理器", "explorer"],
    "任务管理器": ["任务管理器", "task manager"],
    "vscode": ["visual studio code", "vscode", "vs code", "code"],
    "vs code": ["visual studio code", "vscode"],
    "画图": ["画图", "paint"], "cmd": ["命令提示符", "cmd"],
    "powershell": ["powershell"], "steam": ["steam"],
    "qq音乐": ["qq音乐", "qqmusic"], "钉钉": ["钉钉", "dingtalk"],
    "企业微信": ["企业微信", "wxwork"], "飞书": ["飞书", "feishu"],
    "obs": ["obs studio", "obs"], "vlc": ["vlc"], "potplayer": ["potplayer"],
    "剪映": ["剪映", "jianying"], "抖音": ["抖音", "douyin"],
    "百度网盘": ["百度网盘", "baidunetdisk"],
    "阿里云盘": ["阿里云盘", "aliyundrive"],
    "迅雷": ["迅雷", "xunlei", "thunder"], "360": ["360安全卫士", "360浏览器"],
}

SYSTEM_COMMANDS = {"记事本": "notepad.exe", "计算器": "calc.exe",
    "文件管理器": "explorer.exe", "任务管理器": "taskmgr.exe",
    "画图": "mspaint.exe", "cmd": "cmd.exe", "powershell": "powershell.exe"}

SHORTCUT_DIRS = [r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
    os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
    os.path.expanduser("~/Desktop"), r"C:\Users\Public\Desktop", MY_APPS_DIR]


# ==================== 🆕 关闭进程用的别名表 ====================
# 用户说的词 → 可能的 exe 名列表
PROCESS_ALIAS = {
    "cmd": ["cmd.exe"],
    "命令提示符": ["cmd.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
    "记事本": ["notepad.exe"],
    "notepad": ["notepad.exe"],
    "计算器": ["calc.exe", "calculator.exe", "CalculatorApp.exe", "Calculator.exe"],
    "微信": ["WeChat.exe", "Weixin.exe"],
    "wechat": ["WeChat.exe", "Weixin.exe"],
    "chrome": ["chrome.exe"],
    "谷歌浏览器": ["chrome.exe"],
    "浏览器": ["chrome.exe", "msedge.exe", "firefox.exe"],
    "edge": ["msedge.exe"],
    "微软浏览器": ["msedge.exe"],
    "firefox": ["firefox.exe"],
    "vscode": ["Code.exe"],
    "vs code": ["Code.exe"],
    "code": ["Code.exe"],
    "qq": ["QQ.exe", "QQScLauncher.exe", "QQProtect.exe"],
    "网易云": ["cloudmusic.exe"],
    "网易云音乐": ["cloudmusic.exe"],
    "qq音乐": ["QQMusic.exe"],
    "steam": ["steam.exe", "steamwebhelper.exe"],
    "钉钉": ["DingTalk.exe"],
    "飞书": ["Feishu.exe", "Lark.exe"],
    "obs": ["obs64.exe", "obs32.exe"],
    "vlc": ["vlc.exe"],
    "potplayer": ["PotPlayerMini64.exe", "PotPlayerMini.exe"],
    "画图": ["mspaint.exe"],
    "任务管理器": ["Taskmgr.exe"],
    "文件管理器": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "剪映": ["JianyingPro.exe"],
    "抖音": ["Douyin.exe"],
}


def load_learned():
    if not os.path.exists(LEARNED_APPS_FILE): return {}
    try:
        with open(LEARNED_APPS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}


def save_learned(a):
    try:
        with open(LEARNED_APPS_FILE, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=2)
    except: pass


def _scan(kws):
    if not kws: return None
    from core import state
    s = state.get_state()
    ck = kws[0]
    if ck in s.shortcut_cache:
        cached = s.shortcut_cache[ck]
        if os.path.exists(cached): return cached
        else: del s.shortcut_cache[ck]
    all_sc = []
    for bd in SHORTCUT_DIRS:
        if not os.path.exists(bd): continue
        for root, dirs, files in os.walk(bd):
            if root[len(bd):].count(os.sep) > 3: dirs[:] = []; continue
            for f in files:
                if f.lower().endswith(".lnk"): all_sc.append(os.path.join(root, f))
    for kw in kws:
        kl = kw.lower()
        for lnk in all_sc:
            if os.path.splitext(os.path.basename(lnk))[0].lower() == kl:
                s.shortcut_cache[ck] = lnk; return lnk
        for lnk in all_sc:
            if kl in os.path.splitext(os.path.basename(lnk))[0].lower():
                s.shortcut_cache[ck] = lnk; return lnk
    return None


def try_control(text):
    if not text: return None
    tl = text.lower()

    if any(kw in text for kw in ["看看", "看一下", "看下", "摄像头", "屏幕", "看得到我", "看得到"]):
        return None
    if not any(text.startswith(kw) for kw in ["打开", "启动", "运行", "帮我开"]):
        return None
    if len(text) > 12: return None
    if "打开我" in text or "打开你" in text: return None

    an = text
    for kw in ["帮我打开", "帮我启动", "帮我开", "打开", "启动", "运行"]:
        if kw in an:
            an = an.replace(kw, "").strip()
            break
    an = an.strip("，。,.、 呢吧啊呀")
    if not an or len(an) < 2: return None

    learned = load_learned()
    for name, path in learned.items():
        if name in tl or an == name:
            if os.path.exists(path):
                try: subprocess.Popen(f'start "" "{path}"', shell=True)
                except: pass
                return f"已经帮你打开{name}了呢。"
            else:
                del learned[name]; save_learned(learned)
                return f"小家伙，{name}的路径失效了。"
    if an in SYSTEM_COMMANDS:
        try: subprocess.Popen(SYSTEM_COMMANDS[an], shell=True)
        except: pass
        return f"已经帮你打开{an}了呢。"
    for alias, sns in APP_ALIAS.items():
        if alias in tl:
            sp = _scan(sns)
            if sp:
                try: subprocess.Popen(f'start "" "{sp}"', shell=True)
                except: pass
                return f"已经帮你打开{alias}了呢。"
            break
    sp = _scan([an])
    if sp:
        try: subprocess.Popen(f'start "" "{sp}"', shell=True)
        except: pass
        return f"已经打开{an}了呢。"
    return f"__LEARN__{an}"


# ==================== 🆕 查找正在运行的进程 ====================
def find_running_processes(app_name):
    """查找匹配的进程，返回 [(pid, name, mem_mb), ...]"""
    if not app_name: return []
    key = app_name.strip().lower()
    candidates = PROCESS_ALIAS.get(key)
    if candidates is None:
        # 兜底：把用户说的词 + .exe 作为候选
        candidates = [f"{app_name}.exe", f"{app_name.lower()}.exe", f"{app_name.capitalize()}.exe"]

    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                          capture_output=True, text=True, timeout=10,
                          encoding="gbk", errors="ignore")
        if r.returncode != 0: return []
    except Exception as e:
        print(f"[tasklist失败] {e}")
        return []

    matches = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith('"'): continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5: continue
        pname = parts[0]
        try: pid = int(parts[1])
        except: continue
        mem_str = parts[4].replace(",", "").replace(" K", "").replace("K", "").strip()
        try: mem_mb = int(mem_str) // 1024
        except: mem_mb = 0

        pname_lower = pname.lower()
        for c in candidates:
            if (pname_lower == c.lower() or
                pname_lower.replace(".exe", "") == c.lower().replace(".exe", "")):
                matches.append((pid, pname, mem_mb))
                break
    return matches


def kill_processes(pids):
    """强制关闭指定 PID 列表，返回 (成功数, 失败信息列表)"""
    success = 0
    fail = []
    for pid in pids:
        try:
            r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                              capture_output=True, text=True, timeout=10,
                              encoding="gbk", errors="ignore")
            if r.returncode == 0:
                success += 1
            else:
                fail.append(f"PID {pid}: {(r.stderr or r.stdout).strip()}")
        except Exception as e:
            fail.append(f"PID {pid}: {e}")
    return success, fail
# ==================== 🆕 URL 打开 ====================
def open_url(url):
    if not url: return "没指定网址呢。"
    url = url.strip()
    # 补全协议
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import webbrowser
        webbrowser.open(url)
        return f"已经帮你打开 {url} 了呢。"
    except Exception as e:
        return f"打开失败：{e}"
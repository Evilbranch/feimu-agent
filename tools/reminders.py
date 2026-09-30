"""日程提醒 - 支持丰富的时间表达"""
import os
import re
import json
import time
from datetime import datetime, timedelta
from core.constants import REMINDERS_FILE, REMINDER_CHECK_INTERVAL
from core import state


# ==================== 中文数字 → 阿拉伯数字 ====================
_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _cn_to_arabic(text):
    if not text: return text

    def _convert(cn):
        if len(cn) == 1 and cn in _CN_NUM:
            return str(_CN_NUM[cn])
        result = 0
        tmp = 0
        for ch in cn:
            if ch in _CN_NUM:
                tmp = _CN_NUM[ch]
            elif ch in _CN_UNIT:
                unit = _CN_UNIT[ch]
                if tmp == 0: tmp = 1
                result += tmp * unit
                tmp = 0
        result += tmp
        return str(result) if result > 0 else cn

    pattern = r'([零一二三四五六七八九十百千万两]{1,6})'

    def _smart_replace(match):
        num_cn = match.group(1)
        after = text[match.end():match.end()+3]
        if any(u in after for u in ["分钟", "分", "小时", "秒", "点", "时", "天", "周", "礼拜", "星期"]):
            return _convert(num_cn)
        return num_cn

    return re.sub(pattern, _smart_replace, text)


# ==================== 时间表达解析 ====================
_PERIOD_HOUR = {
    "凌晨": (0, 5), "早上": (5, 9), "上午": (9, 12),
    "中午": (12, 13), "下午": (13, 18), "傍晚": (18, 20),
    "晚上": (20, 24), "夜里": (20, 24), "夜里": (20, 24),
}

_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}


def _adjust_hour(h, period):
    """根据上午/下午/晚上调整小时数"""
    if period in ["下午", "傍晚", "晚上", "夜里"] and h < 12:
        return h + 12
    if period == "中午" and h < 12:
        return h + 12
    if period == "凌晨" and h == 12:
        return 0
    return h


def _parse_time(text):
    """
    解析时间表达，返回 (timestamp, cleaned_text) 或 (None, text)
    """
    now = time.time()
    dt_now = datetime.now()
    t = text

    # 1. 同义词归一化
    t = t.replace("半个小时", "30分钟").replace("半小时", "30分钟")
    t = t.replace("一刻钟", "15分钟")
    t = t.replace("一会儿", "5分钟").replace("马上", "1分钟")

    # 2. 中文数字转阿拉伯数字
    t = _cn_to_arabic(t)

    # ========== 相对时间 ==========
    m = re.search(r'(\d+)\s*分钟(?:后|以后|之后|过后)', t)
    if m:
        return now + int(m.group(1)) * 60, t

    m = re.search(r'(\d+)\s*个?\s*小时(?:后|以后|之后|过后)', t)
    if m:
        return now + int(m.group(1)) * 3600, t

    m = re.search(r'(\d+)\s*秒(?:后|以后|之后|过后)', t)
    if m:
        return now + int(m.group(1)), t

    m = re.search(r'(\d+)\s*天(?:后|以后|之后|过后)', t)
    if m:
        return now + int(m.group(1)) * 86400, t

    # ========== 绝对时间 ==========
    # 确定基准日期
    target_date = dt_now.date()
    m = re.search(r'(今天|明天|后天|大后天)', t)
    if m:
        w = m.group(1)
        if w == "明天": target_date += timedelta(days=1)
        elif w == "后天": target_date += timedelta(days=2)
        elif w == "大后天": target_date += timedelta(days=3)

    # 周几 / 下周几
    m = re.search(r'(下+)?(?:周|星期|礼拜)([一二三四五六日天])', t)
    if m:
        prefix = m.group(1) or ""
        wd = _WEEKDAY_MAP.get(m.group(2))
        if wd is not None:
            cur_wd = dt_now.weekday()
            delta = (wd - cur_wd) % 7
            if delta == 0: delta = 7   # 今天说的周几，指下周
            if prefix:
                delta += 7 * len(prefix)
            target_date = dt_now.date() + timedelta(days=delta)

    # 上午/下午/晚上
    period = None
    m = re.search(r'(凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)', t)
    if m: period = m.group(1)

    # X点[X分] / X点半
    m = re.search(r'(\d+)\s*点\s*(半|(\d+)\s*分)?', t)
    if m:
        h = int(m.group(1))
        if m.group(2) == "半":
            mn = 30
        elif m.group(3):
            mn = int(m.group(3))
        else:
            mn = 0
        h = _adjust_hour(h, period)
        try:
            target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=h, minute=mn))
            ts = target_dt.timestamp()
            # 如果时间已过，往后推
            if ts <= now:
                if m and re.search(r'(今天|明天|后天)', t):
                    pass  # 用户指定了日期，不推
                else:
                    ts += 86400
            return ts, t
        except:
            pass

    # 只有"上午/下午"没点
    if period:
        # 默认：上午9点、下午3点、晚上8点
        default_hour = {"凌晨": 3, "早上": 7, "上午": 9, "中午": 12,
                        "下午": 15, "傍晚": 18, "晚上": 20, "夜里": 21}[period]
        try:
            target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=default_hour))
            ts = target_dt.timestamp()
            if ts <= now and not re.search(r'(今天|明天|后天)', t):
                ts += 86400
            return ts, t
        except:
            pass

    # 只说了"明天"、"后天"没具体时间
    if re.search(r'(明天|后天|大后天)', t):
        try:
            target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=9))
            return target_dt.timestamp(), t
        except:
            pass

    return None, text


def _clean_time_words(text, original):
    """从文本里剥掉时间词，留下提醒内容"""
    cleaned = text
    # 相对时间
    cleaned = re.sub(r'\d+\s*分钟(?:后|以后|之后|过后)?', '', cleaned)
    cleaned = re.sub(r'\d+\s*个?\s*小时(?:后|以后|之后|过后)?', '', cleaned)
    cleaned = re.sub(r'\d+\s*秒(?:后|以后|之后|过后)?', '', cleaned)
    cleaned = re.sub(r'\d+\s*天(?:后|以后|之后|过后)?', '', cleaned)
    # 绝对时间
    cleaned = re.sub(r'(今天|明天|后天|大后天)', '', cleaned)
    cleaned = re.sub(r'(下+)?(?:周|星期|礼拜)[一二三四五六日天]', '', cleaned)
    cleaned = re.sub(r'(凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)', '', cleaned)
    cleaned = re.sub(r'\d+\s*点\s*(半|(\d+)\s*分)?', '', cleaned)
    # 提醒动词
    for pat in ["提醒我", "提醒一下", "记得提醒我", "叫我", "提醒"]:
        if pat in cleaned:
            idx = cleaned.find(pat)
            cleaned = cleaned[idx+len(pat):].strip()
            break
    cleaned = cleaned.strip(" ，。,.、")
    return cleaned or "事情"


def _load():
    if not os.path.exists(REMINDERS_FILE): return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []


def _save(r):
    try:
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    except: pass


def add(text):
    if not text or "提醒" not in text and "叫我" not in text and "记得" not in text:
        # 检查是否含时间词
        if not re.search(r'(后|以后|之后|点|明天|后天|今天|周|星期|分钟|小时|秒)', text):
            return None

    tt, parsed = _parse_time(text)
    if tt is None: return None

    content = _clean_time_words(parsed, text)

    r = _load()
    r.append({"time": tt, "content": content, "done": False, "created": time.time()})
    _save(r)

    d = tt - time.time()
    if d < 60: td = f"{int(d)} 秒后"
    elif d < 3600: td = f"{int(d/60)} 分钟后"
    elif d < 86400:
        # 显示绝对时间
        td = datetime.fromtimestamp(tt).strftime("%H:%M")
    else:
        td = datetime.fromtimestamp(tt).strftime("%m月%d日 %H:%M")
    return f"好的，{td}我会提醒你{content}哦。"


def list_pending():
    r = _load()
    now = time.time()
    pending = [item for item in r if not item.get("done", False)]
    if not pending:
        return "当前没有任何待办提醒。"
    lines = []
    for item in sorted(pending, key=lambda x: x["time"]):
        delta = item["time"] - now
        if delta < 0:
            td = "已过期"
        elif delta < 60:
            td = f"{int(delta)}秒后"
        elif delta < 3600:
            td = f"{int(delta/60)}分钟后"
        elif delta < 86400:
            td = datetime.fromtimestamp(item["time"]).strftime("%H:%M")
        else:
            td = datetime.fromtimestamp(item["time"]).strftime("%m月%d日 %H:%M")
        lines.append(f"· {item['content']}（{td}）")
    return "当前待办提醒：\n" + "\n".join(lines)


def check_loop():
    s = state.get_state()
    while not s.shutdown_flag.is_set():
        try:
            r = _load(); now = time.time(); changed = False
            for item in r:
                if not item.get("done", False) and item["time"] <= now:
                    item["done"] = True; changed = True
                    with s.reminder_queue_lock:
                        s.reminder_queue.append(item["content"])
            if changed: _save(r)
        except: pass
        s.shutdown_flag.wait(REMINDER_CHECK_INTERVAL)


def pop():
    s = state.get_state()
    with s.reminder_queue_lock:
        if s.reminder_queue: return s.reminder_queue.pop(0)
    return None
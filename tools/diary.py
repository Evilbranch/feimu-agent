"""日记 + 每日总结 + 记忆纠正"""
import os
import json
import time
import re
from datetime import datetime
from core.constants import DAILY_LOG_DIR, DIARY_FILE, DAILY_LOG_KEEP_DAYS


def cleanup():
    if not os.path.exists(DAILY_LOG_DIR): return
    try:
        now = time.time(); cutoff = now - DAILY_LOG_KEEP_DAYS * 86400
        for fn in os.listdir(DAILY_LOG_DIR):
            if not fn.endswith(".json"): continue
            fp = os.path.join(DAILY_LOG_DIR, fn)
            try:
                if os.path.getmtime(fp) < cutoff: os.remove(fp)
            except: pass
    except: pass


def _today_file():
    if not os.path.exists(DAILY_LOG_DIR): os.makedirs(DAILY_LOG_DIR)
    return os.path.join(DAILY_LOG_DIR, f"{time.strftime('%Y-%m-%d')}.json")


def append(ui, ai):
    try:
        lf = _today_file()
        entry = {"time": time.strftime("%H:%M:%S"), "user": ui, "ai": ai}
        logs = []
        if os.path.exists(lf):
            try:
                with open(lf, "r", encoding="utf-8") as f: logs = json.load(f)
            except: logs = []
        logs.append(entry)
        with open(lf, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except: pass


def _today_logs():
    lf = _today_file()
    if not os.path.exists(lf): return []
    try:
        with open(lf, "r", encoding="utf-8") as f: return json.load(f)
    except: return []


def _load_diary():
    if not os.path.exists(DIARY_FILE): return []
    try:
        with open(DIARY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []


def _save_diary(d):
    try:
        with open(DIARY_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except: pass


# ==================== 元对话过滤 ====================
def _is_meta_conversation(user_text):
    """判断这轮用户输入是否是"元对话"（总结、日记、报告、提醒查询、系统调整）"""
    if not user_text: return True
    meta_kw = [
        # 日记/总结操作
        "总结", "终结", "終結", "复盘", "複盤", "写日记", "寫日記", "日记", "日記",
        "重写", "重寫", "报告", "報告", "回顾", "回顧", "前天", "昨天聊", "昨天我們聊",
        "昨天我们聊", "今天聊", "今天我們聊", "今天我们聊", "聊过什么", "聊過什麼",
        "聊了什么", "聊了什麼",
        # 提醒查询
        "我有什么提醒", "我有什麼提醒", "还有几分钟", "還有幾分鐘",
        "什么时候提醒", "什麼時候提醒", "提醒呢", "闹钟呢", "鬧鐘呢",
        # 元对话（防止污染日记）
        "改了", "改成", "优化", "優化", "提示词", "提示詞", "硬编码", "硬編碼",
        "代码", "代碼", "系统", "系統", "模型", "L1", "L2", "L3",
        "帮你改", "幫你改", "帮你优", "幫你優",
    ]
    for kw in meta_kw:
        if kw in user_text:
            return True
    return False


def generate_summary(client, provider):
    logs = _today_logs()
    if len(logs) < 3:
        return "小家伙，今天聊得太少啦，再陪我聊会儿嘛。"

    real_logs = [e for e in logs if not _is_meta_conversation(e.get("user", ""))]
    if len(real_logs) < 2:
        return "今天没什么特别的，就陪了小家伙一会儿。"

    conv = "".join([f"[{e['time']}] 用户：{e['user']}\n绯木：{e['ai']}\n\n" for e in real_logs])
    prompt = (
        f"你是绯木，成熟温柔知性的御姐，用第一人称'我'写日记。\n"
        f"根据今天的对话，写一篇 80 字以内的简短日记。\n"
        f"要求：\n"
        f"1. 只写今天真实发生的事，不要写'我们讨论了总结内容'这类空话。\n"
        f"2. 用第一人称'我'描述，例如'今天小家伙问我...'。\n"
        f"3. '小家伙'这个词整篇日记里最多出现 1 次，不要反复说。\n"
        f"4. 不要 markdown、列表符号、括号编号。\n"
        f"5. 抓住今天最重要的 1~2 件事，越自然越好。\n"
        f"6. 如果对话里没有值得记录的实事，就说'今天没什么特别的，陪了小家伙一会儿'。\n\n"
        f"对话记录：\n{conv[:3000]}\n\n直接输出日记（不要前缀、不要引号）："
    )
    try:
        r = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": prompt}],
            timeout=60, temperature=0.8, max_tokens=200,
            extra_body={"keep_alive": "30m"},
        )
        summary = r.choices[0].message.content.strip()
        for sym in ["\n- ", "\n* ", "\n· ", "- ", "* ", "· "]:
            summary = summary.replace(sym, "")
        summary = re.sub(r'^(日记|日記)[：:]\s*', '', summary)
    except Exception as e:
        summary = f"[生成日记失败] {e}"

    today = time.strftime("%Y-%m-%d")
    diary = _load_diary()
    diary = [d for d in diary if d.get("date") != today]
    diary.append({"date": today, "summary": summary, "message_count": len(real_logs)})
    diary.sort(key=lambda x: x["date"])
    _save_diary(diary)
    return summary


# ==================== 智能截断 + 清洗 ====================
def _smart_truncate(text, max_len=100):
    if len(text) <= max_len:
        return text
    for punct in ["。", "！", "？", "；"]:
        idx = text.rfind(punct, 0, max_len)
        if idx > max_len * 0.5:
            return text[:idx+1]
    return text[:max_len] + "……"


def _clean_diary_text(text):
    """清理日记里的元对话碎片"""
    if not text: return text
    # 去掉"我帮你改了..."这类元对话
    text = re.sub(r'我帮你改了[^。]*。', '', text)
    text = re.sub(r'我幫你改了[^。]*。', '', text)
    text = re.sub(r'我将大部分[^。]*。', '', text)
    text = re.sub(r'我將大部分[^。]*。', '', text)
    text = re.sub(r'帮你改[^。]*。', '', text)
    text = re.sub(r'幫你改[^。]*。', '', text)
    text = re.sub(r'改了你的[^。]*。', '', text)
    text = re.sub(r'改成[A-Za-z0-9]{1,5}[^。]*。', '', text)
    # 清理重复标点
    text = re.sub(r'。+', '。', text)
    text = re.sub(r'，+', '，', text)
    return text.strip()


def query_past(ui):
    diary = _load_diary()
    if not diary: return "我们还没写过日记呢。"
    td = None
    if "昨天" in ui: td = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    elif "前天" in ui: td = time.strftime("%Y-%m-%d", time.localtime(time.time() - 2*86400))
    elif "上周" in ui: td = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7*86400))

    if td:
        for d in diary:
            if d["date"] == td:
                summary = re.sub(r'^\d{4}年\d+月\d+日\s*星期[一二三四五六日]\s*\S*\n*', '', d["summary"])
                summary = _clean_diary_text(summary)
                return _smart_truncate(summary, 100)
        return "那天没有写日记呢。"

    if "这两天" in ui or "這兩天" in ui or "最近" in ui:
        recent = sorted(diary, key=lambda x: x["date"])[-2:]
        parts = []
        for d in recent:
            s = re.sub(r'^\d{4}年\d+月\d+日\s*星期[一二三四五六日]\s*\S*\n*', '', d["summary"])
            s = _clean_diary_text(s)
            parts.append(_smart_truncate(s, 50))
        return "。".join(parts)

    latest = sorted(diary, key=lambda x: x["date"])[-1]
    summary = re.sub(r'^\d{4}年\d+月\d+日\s*星期[一二三四五六日]\s*\S*\n*', '', latest["summary"])
    summary = _clean_diary_text(summary)
    return _smart_truncate(summary, 100)


# ==================== 记忆纠正 ====================
def delete_diary(date=None):
    if date is None:
        date = time.strftime("%Y-%m-%d")
    diary = _load_diary()
    before = len(diary)
    diary = [d for d in diary if d.get("date") != date]
    if len(diary) == before:
        return f"{date} 那天没有日记记录呢。"
    _save_diary(diary)
    return f"已删除 {date} 的日记。"


def set_diary_content(content, date=None):
    if date is None:
        date = time.strftime("%Y-%m-%d")
    diary = _load_diary()
    diary = [d for d in diary if d.get("date") != date]
    diary.append({"date": date, "summary": content.strip(), "message_count": 0})
    diary.sort(key=lambda x: x["date"])
    _save_diary(diary)
    return f"已更新 {date} 的日记：{content[:50]}"


def list_diary_dates():
    diary = _load_diary()
    if not diary: return "还没有写过任何日记呢。"
    dates = sorted([d["date"] for d in diary])
    return "有日记的日期：" + "、".join(dates)


def regenerate_today(client, provider):
    today = time.strftime("%Y-%m-%d")
    delete_diary(today)
    return generate_summary(client, provider)
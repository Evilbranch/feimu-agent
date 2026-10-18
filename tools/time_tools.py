"""时间工具 - 让 AI 准确理解时间"""
import time
from datetime import datetime, timedelta

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def get_current_time():
    """返回当前时间的完整信息"""
    now = datetime.now()
    wd = _WEEKDAYS[now.weekday()]
    return f"现在是 {now.strftime('%Y年%m月%d日')} {wd} {now.strftime('%H:%M:%S')}"


def calculate_date(operation="add", days=0, target_date=None):
    """
    operation:
      - "add": 从今天加 days 天
      - "diff": 算 target_date 距离今天多少天
    """
    today = datetime.now().date()

    if operation == "add":
        try:
            days = int(days)
        except:
            return "天数格式不对呢。"
        result = today + timedelta(days=days)
        wd = _WEEKDAYS[result.weekday()]
        if days >= 0:
            return f"{days}天后是 {result.strftime('%Y年%m月%d日')}（{wd}）"
        else:
            return f"{abs(days)}天前是 {result.strftime('%Y年%m月%d日')}（{wd}）"

    if operation == "diff":
        if not target_date:
            return "没指定目标日期呢。"
        try:
            target = datetime.strptime(target_date, "%Y-%m-%d").date()
        except:
            return f"日期格式不对：{target_date}，应该是 YYYY-MM-DD"
        diff = (target - today).days
        if diff > 0:
            return f"距离 {target_date} 还有 {diff} 天"
        elif diff < 0:
            return f"{target_date} 已经过去 {abs(diff)} 天了"
        else:
            return f"就是今天呢！"
    return "不支持的 operation。"
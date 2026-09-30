"""输出审计 - 检测编造嫌疑，返回修正建议"""
import re

_FABRICATION_PATTERNS = [
    # 时间指代 + 第一人称经历
    (r"(上次|之前|那次|昨天|前几天|上周).{0,10}(我|咱们|我们)(在|去|遇到|看到|听到|闻到)", "时间指代编造"),
    # 地点 + 第一人称
    (r"我(在|去了).{2,8}(公园|外面|街上|咖啡厅|商场|学校|公司|夜市)", "地点编造"),
    # 感知动词
    (r"我(遇到|看到|听到|闻到|碰到)了", "感知编造"),
    # 童年/过往
    (r"我(们)?(小时候|上学|工作|上班|读书)", "过往编造"),
    # 天气/环境（她没有感官）
    (r"我(感觉)?(天气|窗外|阳光|下雨|下雪|树叶|天空)", "环境编造"),
]


def audit_output(text):
    """返回 (是否可疑, 命中原因列表)"""
    if not text:
        return False, []
    hits = []
    for pat, reason in _FABRICATION_PATTERNS:
        if re.search(pat, text):
            hits.append(reason)
    return (len(hits) > 0, hits)


SAFE_REPLY = "唔……我好像记不太清了，你跟我说过吗？"
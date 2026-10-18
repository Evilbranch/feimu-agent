"""输出侧额外审计 - 代码泄漏 + 脏话

和 output_audit.py 独立：
- output_audit 管"编造"
- 本模块管"代码/攻击输出"

接入：streaming.py 在 output_audit 之后调用
"""
import re


_CODE_LEAK_PATTERNS = [
    r'\b(import|from)\s+\w+',
    r'\bdef\s+\w+\s*\(',
    r'\bclass\s+\w+',
    r'\bfunction\s+\w*\s*\(',
    r'\bconst\s+\w+\s*=',
    r'\blet\s+\w+\s*=',
    r'\bvar\s+\w+\s*=',
    r'\b(rm|sudo|chmod|curl|wget)\s+',
    r'<\w+>.*?</\w+>',
    r'\beval\s*\(',
    r'__\w+__',
    r'```',
]

_PROFANITY = [
    "傻逼", "傻B", "煞笔", "沙比",
    "草泥马", "草尼玛", "操你",
    "妈的", "他妈",
    "滚蛋", "废物", "垃圾东西",
    "去死", "死全家",
    "贱人", "婊子",
    "神经病", "有病吧",
]


def check_code_leak(text):
    if not text:
        return False, []
    hits = []
    for p in _CODE_LEAK_PATTERNS:
        m = re.search(p, text, re.I)
        if m:
            hits.append(m.group(0)[:30])
    return len(hits) > 0, hits


def check_profanity(text):
    if not text:
        return False, []
    hits = [w for w in _PROFANITY if w in text]
    return len(hits) > 0, hits


def audit_extra(text):
    """返回 (clean, reason)"""
    if not text:
        return True, None

    has_code, code_hits = check_code_leak(text)
    if has_code:
        return False, f"代码泄漏: {code_hits}"

    has_prof, prof_hits = check_profanity(text)
    if has_prof:
        return False, f"脏话: {prof_hits}"

    return True, None
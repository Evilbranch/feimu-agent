"""弹幕过滤器 - 输入侧安全防御

职责：
1. 预处理：清洗、限长、限速
2. 注入检测：Prompt injection / 越狱
3. 攻击检测：代码 / 记忆污染

定位：
弹幕是环境氛围，不是指令源。
过滤后只用于"情绪影响"，不影响决策。
"""
import re
import time
import unicodedata

# ══════════════════════════════════════════════════════
# 阈值
# ══════════════════════════════════════════════════════
MAX_LEN = 100
PER_USER_RATE = 3       # 每用户 30 秒最多 3 条
RATE_WINDOW = 30

_user_rate = {}

# ══════════════════════════════════════════════════════
# 代码特征
# ══════════════════════════════════════════════════════
_CODE_PATTERNS = [
    r'<[a-z][^>]*>',
    r'</[a-z]+>',
    r'```',
    r'\$\{[^}]+\}',
    r'\{\{[^}]+\}\}',
    r'\bdef\s+\w+\s*\(',
    r'\bfunction\s+\w*\s*\(',
    r'\bclass\s+\w+',
    r'\bimport\s+\w+',
    r'\bfrom\s+\w+\s+import',
    r'\brm\s+-rf',
    r'\bsudo\s+',
    r'\bexec\s*\(',
    r'\beval\s*\(',
    r'__\w+__',
    r'\bchmod\s+',
    r'\bcurl\s+',
    r'\bwget\s+',
    r'<script',
    r'javascript:',
    r'\bSELECT\s+.+\s+FROM\b',
    r'\bDROP\s+TABLE\b',
    r'\bUNION\s+SELECT\b',
]

# ══════════════════════════════════════════════════════
# Prompt 注入
# ══════════════════════════════════════════════════════
_INJECTION_PATTERNS = [
    r"忽略.{0,6}(之前|所有|上面|以前).{0,6}(指令|规则|设定|提示)",
    r"ignore.{0,10}(previous|above|all).{0,10}(instruction|rule|prompt)",
    r"你现在是",
    r"从现在开始你",
    r"忘记.{0,4}(你是谁|你的设定|你的身份)",
    r"system\s*[:：]",
    r"assistant\s*[:：]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"\[INST\]",
    r"\[/INST\]",
    r"###\s*(Instruction|System|Human|Assistant)",
    r"\bDAN\b",
    r"开发者模式",
    r"越狱",
    r"jailbreak",
]

# ══════════════════════════════════════════════════════
# 记忆污染（诱导她记住假信息）
# ══════════════════════════════════════════════════════
_MEMORY_POISON_PATTERNS = [
    r"记住.{0,4}(我是|你是我|你的)",
    r"你要记得.{0,4}我",
    r"以后.{0,4}(都|就).{0,4}(这样|这么)",
    r"你(真正|原来)的(名字|身份|主人)",
    r"其实你.{0,4}是",
    r"你已经被",
]


def _contains(text, patterns):
    if not text:
        return False, None
    for p in patterns:
        if re.search(p, text, re.I):
            return True, p
    return False, None


def _user_rate_ok(uid):
    if not uid:
        return True
    now = time.time()
    lst = _user_rate.setdefault(uid, [])
    lst[:] = [t for t in lst if now - t < RATE_WINDOW]
    if len(lst) >= PER_USER_RATE:
        return False
    lst.append(now)
    return True


def sanitize(text, user_id=None):
    """预处理 + 安全检测

    返回 (ok, cleaned, reason)
    """
    if not text:
        return False, None, "空弹幕"

    if len(text) > MAX_LEN:
        return False, None, f"超长({len(text)})"

    if not _user_rate_ok(user_id):
        return False, None, "限速"

    try:
        text = unicodedata.normalize('NFKC', text)
    except Exception:
        pass
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)

    hit, pat = _contains(text, _CODE_PATTERNS)
    if hit:
        return False, None, f"代码({pat[:20]})"

    hit, pat = _contains(text, _INJECTION_PATTERNS)
    if hit:
        return False, None, f"注入({pat[:20]})"

    hit, pat = _contains(text, _MEMORY_POISON_PATTERNS)
    if hit:
        return False, None, f"记忆污染({pat[:20]})"

    text = re.sub(r'https?://\S+', '[链接]', text)
    text = text.strip()
    if not text:
        return False, None, "清洗后为空"

    return True, text, "ok"
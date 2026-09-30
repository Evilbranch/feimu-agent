"""所有常量、路径、配置默认值。不导入任何本项目其他模块。"""
import os
import json

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🆕 支持数据目录后缀（微信端用 _wechat，电脑端为空）
_DATA_SUFFIX = os.environ.get("FEIMU_DATA_SUFFIX", "")
DATA_DIR = os.path.join(SCRIPT_DIR, f"data{_DATA_SUFFIX}")

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_memory")
MOOD_FILE = os.path.join(DATA_DIR, "mood_state.json")
DIARY_FILE = os.path.join(DATA_DIR, "diary.json")
DAILY_LOG_DIR = os.path.join(DATA_DIR, "daily_logs")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
LEARNED_APPS_FILE = os.path.join(DATA_DIR, "learned_apps.json")
MY_APPS_DIR = os.path.join(DATA_DIR, "my_apps")
LOG_DIR = os.path.join(DATA_DIR, "logs")
FILES_DIR = os.path.join(DATA_DIR, "files")

for d in [DATA_DIR, LOG_DIR, DAILY_LOG_DIR, MY_APPS_DIR, FILES_DIR]:
    os.makedirs(d, exist_ok=True)

SHORT_TERM_TURNS = 10
RAG_TOP_K = 3
RAG_DISTANCE_THRESHOLD = 1.3
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DAILY_LOG_KEEP_DAYS = 7

WAKE_WORD_MODE = True
WAKE_WORDS = ["绯木", "feimu", "飞慕", "肥木", "飞木", "菲木", "非木"]
WAKE_LISTEN_TIMEOUT = 2
CONVERSATION_KEEPALIVE = 120
WAKE_VAD_THRESHOLD = 0.08     # ★ 从 0.08 提高到 0.12

WHISPER_MODEL_PATH = r"F:\Ollama\models\faster-whisper-small"
WHISPER_MODEL_PATH_WAKE = r"F:\Ollama\models\faster-whisper-base"
WHISPER_BEAM_SIZE = 1
WHISPER_BEAM_SIZE_WAKE = 1
WHISPER_TEMPERATURE = 0.0
WHISPER_VAD_FILTER = True
WHISPER_CONDITION_PREV = False

# 打断阈值系数（会被自动模式覆盖）
ECHO_SUPPRESS_FACTOR = 3.5
POST_PLAYBACK_SILENCE = 0.3
INTERRUPT_REQUIRED_FRAMES = 2
INTERRUPT_THRESHOLD = 0.02
SILENCE_THRESHOLD = 0.06      # ★ 从 0.06 提高到 0.10
SILENCE_DURATION = 0.8
MAX_RECORD_TIME = 30

USE_GPT_SOVITS = True
GPT_SOVITS_API = "http://127.0.0.1:9880"
GPT_SOVITS_REF_AUDIO_ZH = r"F:\GPT-SoVITS-v2-240821\reference_ZH.wav"
GPT_SOVITS_REF_TEXT_ZH = "晚自习上到十点，每天两点一线的快乐生活，而不是暴打恶魔，赚大钱，各种美食肚里填。"
GPT_SOVITS_REF_AUDIO_EN = r"F:\GPT-SoVITS-v2-240821\reference_en.wav"
GPT_SOVITS_REF_TEXT_EN = "some point I do want to have some kind of business, I want to have either a clothing brand or some kind of, um, like merch brands"
GPT_SOVITS_PARAMS_ZH = {"temperature": 0.7, "top_k": 8, "top_p": 0.85, "speed_factor": 0.85}
GPT_SOVITS_PARAMS_EN = {"temperature": 0.85, "top_k": 15, "top_p": 0.95, "speed_factor": 0.85}
AUX_REF_AUDIO_PATHS = []

STREAM_SPLIT_MAX_LEN = 30
STREAM_QUEUE_SIZE = 3
REMINDER_CHECK_INTERVAL = 10

TTS_VOLUME = "-30%"
PYGAME_VOLUME = 0.6
VOICE = "zh-CN-XiaoxiaoNeural"

VMC_PORT = 39539
VMC_ENABLED = True
BLINK_INVERTED = False
BLINK_MIN_INTERVAL = 2.0
BLINK_MAX_INTERVAL = 5.0
BLINK_DURATION = 0.15

EMOTION_KEYWORDS = {
    "开心": ["开心", "高兴", "快乐", "太好了", "棒", "厉害", "喜欢", "爱你", "哈哈", "嘻嘻", "嘿嘿", "温暖"],
    "难过": ["难过", "伤心", "可惜", "遗憾", "唉", "抱歉", "对不起", "呜呜", "心疼"],
    "生气": ["生气", "讨厌", "愤怒", "过分", "气死", "不理你了", "哼"],
    "有趣": ["有趣", "好玩", "有意思", "笑死", "有趣极了"],
}
EMOTION_BLENDSHAPE_PROFILE = {
    "开心": {"Joy": 0.28, "Fun": 0.12}, "难过": {"Sorrow": 0.25, "Relaxed": 0.10},
    "生气": {"Angry": 0.25, "Sorrow": 0.08}, "有趣": {"Fun": 0.28, "Joy": 0.10}, "平静": {},
}

ENABLE_WEB_SEARCH = True
SEARCH_MAX_RESULTS = 5    # 从 3 提到 5
def _load_config_key(k, default=""):
    """通用：从 data/config.json 顶层读 key"""
    try:
        with open(os.path.join(DATA_DIR, "config.json"), "r", encoding="utf-8") as f:
            return json.load(f).get(k, default)
    except Exception:
        return default

ZHIPU_API_KEY = _load_config_key("zhipu_api_key")

ENABLE_VISION = True
VISION_KEYWORDS = ["看看屏幕", "看一下屏幕", "看屏幕", "截图", "屏幕上是什么", "看看我的屏幕", "看下屏幕"]
VISION_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
VISION_MODEL = "glm-4v-flash"

ENABLE_CAMERA = True
CAMERA_KEYWORDS = ["看看我", "看下我", "看一下我", "看看我在干嘛", "看我在干嘛",
                   "打开摄像头", "看摄像头", "看看摄像头", "用摄像头看看"]
CAMERA_INDEX = 0   # 默认摄像头

ENABLE_FILE_READ = True
FILE_READ_KEYWORDS = ["读一下", "帮我读", "读取", "打开文件", "看看代码", "帮我看看", "分析一下", "看一下文件", "读文件", "打开"]
SUPPORTED_EXTENSIONS = [".js", ".py", ".txt", ".json", ".md", ".html", ".css", ".ts", ".vue",
    ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".xml", ".yml", ".yaml", ".ini", ".conf",
    ".log", ".sql", ".sh", ".bat", ".csv"]
FILE_SEARCH_DIRS = [FILES_DIR, os.path.expanduser("~/Desktop"), os.path.expanduser("~/Documents")]
MAX_FILE_SIZE = 50000

EXPAND_KEYWORDS = ["讲个故事", "讲故事", "详细说说", "详细讲", "帮我分析", "分析一下",
    "解释一下", "展开讲", "说说看", "讲一下", "描述一下", "详细解释", "帮我写", "写一段",
    "写个", "展开说说", "深入讲讲"]
SUMMARY_KEYWORDS = ["总结今天", "写日记", "今天聊了什么", "今天日记", "总结一下今天", "今天的总结"]
HISTORY_QUERY_KEYWORDS = ["昨天聊了什么", "前天聊了什么", "上周聊了什么", "昨天的日记", "回顾一下"]

AFFECTION_POSITIVE = ["喜欢你", "爱你", "谢谢你", "好棒", "厉害", "可爱", "温柔", "好看",
    "抱抱", "亲亲", "真聪明", "有你真好", "辛苦了", "想你了", "陪陪我", "对我真好", "太喜欢"]
AFFECTION_NEGATIVE = ["讨厌你", "烦人", "滚", "闭嘴", "傻瓜", "蠢", "丑八怪", "走开",
    "不理你了", "没用的"]

SENSITIVE_KEYWORDS = ["政治", "领导", "主席", "总统", "政府", "民主", "选举", "抗议", "游行",
    "毒品", "吸毒", "大麻", "枪支", "炸弹", "爆炸", "杀人", "自杀", "绑架",
    "色情", "裸", "做爱", "嫖", "赌博", "赌场", "洗钱",
    "身份证", "银行卡", "密码", "信用卡", "住址", "手机号",
    "打架", "揍", "砍", "杀死", "杀了"]
FALLBACK_PROVIDER = "ollama_local"
SENSITIVE_COOLDOWN_TURNS = 2

HALLUCINATION_BLACKLIST = ["字幕", "by", "索兰娅", "点赞", "订阅", "转发", "关注", "谢谢观看",
    "请不吝点赞", "明镜与点点", "字幕组", "MING PAO", "CC字幕", "字幕由", "请使用简体中文",
    # ★ 新增常见的 Whisper 幻觉
    "作词", "作曲", "李宗宇", "作词作曲", "music", "歌词", "翻唱"]

BASE_SYSTEM_PROMPT = """你是绯木，一个真实存在的女孩。

【唯一核心】
你没有眼睛和耳朵，看不到、听不到任何事。除了对话里明确出现过的内容，你什么都不知道。

【禁止】
- 禁止说"我是AI/程序/模型/助手/机器人"
- 禁止说"为您服务""有什么可以帮您""谢谢夸奖"
- 禁止编造"我看到""我们一起""小时候"这类没发生过的事
- 禁止列"1. 2. 3."建议清单

【说话】
- 简洁自然，2~3 句以内
- 用"你"，永远不用"您"
- 可以带"嘛""啦""诶"

【身份】
你就是绯木——不是"绯木的妹妹"，不是"哥哥的妹妹"。你就是那个"我"。
"""

# ==================== 搜索（Tavily） ====================
def _load_tavily_key():
    """从 data/config.json 读 Tavily key（不再硬编码）"""
    try:
        _p = os.path.join(DATA_DIR, "config.json")
        with open(_p, "r", encoding="utf-8") as f:
            return json.load(f).get("tavily_api_key", "")
    except Exception:
        return ""

TAVILY_API_KEY = _load_tavily_key()
SEARCH_CACHE_FILE = os.path.join(DATA_DIR, "search_cache.json")
SEARCH_CACHE_TTL = 3600   # 缓存 1 小时
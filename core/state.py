"""全局状态容器。所有跨模块共享的变量都放这里。"""
import threading
import time


class State:
    def __init__(self):
        # 当前输入来源：owner / friend / guest / mc / audience
                # 内在生活循环
        self.inner_life_enabled = True
        self.hourly_speak_count = 0
        self.hourly_reset_time = time.time()
        self.current_source = "owner"
        self.whisper_model = None
        self.whisper_model_wake = None
        self.whisper_ready = threading.Event()
        self.rag = None
        self.mood_mgr = None
        self.vmc_client = None
        self.life_sim = None
        self.audio_device = None
        self.shutdown_flag = threading.Event()
        self.last_interrupted = False
        self.learning_state = {"active": False, "stage": None,
                               "app_name": None, "candidate_path": None}
        self.reminder_queue = []
        self.reminder_queue_lock = threading.Lock()
        self.shortcut_cache = {}
        self.text_mode = False
        self.mute_mode = False
        self.interrupt_listen = threading.Event()
        # 说话人相关
        self.current_speaker = "主人"
        self.last_speaker = None
        self.live_mode = False
        # 主动关心
        self.last_interaction_time = time.time()
        self.last_proactive_time = 0.0
        self.proactive_enabled = True
        self.proactive_interval = 600
        self.proactive_missed = 0
        # 🆕 主动消息队列（后台线程 → 主循环）
        self.proactive_queue = []
        self.proactive_queue_lock = threading.Lock()
        self.recent_proactive_msgs = []


_state = None


def get_state():
    global _state
    if _state is None:
        _state = State()
    return _state
"""感知总线 - 统一处理视觉、MC、未来的弹幕等外部信息"""
import time
import threading
import random
from brain.urge import add_urge

# ══════════════════════════════════════════════════════════════
# ★ 新增：弹幕临时缓冲池
# 用于承接直播时瞬间爆发的弹幕，不直接写 Urge，而是定时聚合
# ══════════════════════════════════════════════════════════════
_danmu_buffer = []
_danmu_lock = threading.Lock()
_danmu_worker_started = False


def ingest(source, content, emotion="平静", pressure=0.4):
    """感知总线入口：接收视觉、MC、摄像头、观众弹幕等输入"""
    _reason_map = {
        "screen": "刚才看哥哥打游戏",
        "mc": "刚才在游戏里",
        "camera": "刚才看着哥哥",
        "audience": "观众刚才在发弹幕"
    }
    reason_str = _reason_map.get(source, f"感知到{source}")

    # ★ 1. 如果是观众弹幕，进入缓冲池，不直接写 Urge
    if source == "audience":
        with _danmu_lock:
            _danmu_buffer.append(content)
        return  # 拦截，不往下走

    # 2. 哥哥的话/视觉/MC，正常写 Urge
    add_urge(
        content=content,
        reason=reason_str,
        base_pressure=pressure,
        source=source,
        action_type="speak"
    )
    
    # 3. 写入 L2 情景记忆（可选，保持原有逻辑）
    try:
        # ★ 核心降噪：屏幕截图产生的废话（source="screen"），绝不写入长期记忆！
        if source == "screen":
            return

        from brain.episodic import record_episode
        from brain.persona import get_current_state
        
        BAD_WORDS = ["玩什么游戏", "建筑太高", "游戏里的", "仿佛", "童年"]
        if not any(w in content for w in BAD_WORDS):
            entities = ["屏幕"] if source == "screen" else (["MC世界"] if source == "mc" else ["摄像头"])
            record_episode(
                content=content,
                event_type="interaction",
                entities=entities,
                self_emotion=get_current_state().get("emotion"),
                self_intent="observe",
                self_role="observer",
                channel=source
            )
    except Exception as e:
        print(f"[感知总线] 写入L2失败: {e}")


# ══════════════════════════════════════════════════════════════
# ★ 弹幕聚合后台线程
# ══════════════════════════════════════════════════════════════
def _danmu_worker():
    """每 10 秒唤醒一次，处理累积的弹幕，过滤无效信息，合并打包推入 Urge"""
    while True:
        time.sleep(10)
        with _danmu_lock:
            if not _danmu_buffer:
                continue
            
            # 提取出有意义的弹幕（过滤纯表情、太短、无中文的）
            valid_danmu = []
            for d in _danmu_buffer:
                d = (d or "").strip()
                # 长度小于2的丢掉，全无汉字的丢掉
                if len(d) < 2 or not any('\u4e00' <= c <= '\u9fa5' for c in d):
                    continue
                valid_danmu.append(d)
            
            # 清空缓冲区
            _danmu_buffer.clear()
            
            if not valid_danmu:
                continue
                
            # 如果超过 3 条，选取带问号的，或者随机抽取
            if len(valid_danmu) > 3:
                # 优先挑带问号的
                questions = [d for d in valid_danmu if "?" in d or "？" in d or "吗" in d or "什么" in d]
                if questions:
                    selected = questions[:2]
                else:
                    selected = random.sample(valid_danmu, min(2, len(valid_danmu)))
                merged_content = f"刚才弹幕里有很多人说话，我只注意到了这些：{'；'.join(selected)}"
            else:
                # 弹幕较少，直接拼接
                merged_content = f"刚才观众发弹幕说：{'；'.join(valid_danmu)}"

            # 包装成冲动，推入队列
            # 因为经过了 10 秒聚合，不需要过高的压力，给 0.3 即可
            add_urge(
                content=merged_content,
                reason="观众的弹幕",
                base_pressure=0.3, 
                source="audience",
                action_type="speak"
            )

# 在模块加载时自动启动线程，但做防重入保护
def _start_danmu_worker_once():
    global _danmu_worker_started
    if not _danmu_worker_started:
        t = threading.Thread(target=_danmu_worker, daemon=True)
        t.start()
        _danmu_worker_started = True

_start_danmu_worker_once()
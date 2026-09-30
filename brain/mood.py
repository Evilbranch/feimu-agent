"""好感度 + 情绪"""
import os
import json
import time
from core.constants import MOOD_FILE, AFFECTION_POSITIVE, AFFECTION_NEGATIVE


class MoodManager:
    def __init__(self):
        self.affection = 50; self.current_emotion = "平静"
        self.emotion_until = 0.0
        self.last_seen_date = time.strftime("%Y-%m-%d")
        self._load(); self._check_decay()

    def _load(self):
        if not os.path.exists(MOOD_FILE): self._save(); return
        try:
            with open(MOOD_FILE, "r", encoding="utf-8") as f: d = json.load(f)
            self.affection = d.get("affection", 50)
            self.current_emotion = d.get("current_emotion", "平静")
            self.emotion_until = d.get("emotion_until", 0.0)
            self.last_seen_date = d.get("last_seen_date", time.strftime("%Y-%m-%d"))
        except: pass

    def _save(self):
        try:
            with open(MOOD_FILE, "w", encoding="utf-8") as f:
                json.dump({"affection": self.affection,
                           "current_emotion": self.current_emotion,
                           "emotion_until": self.emotion_until,
                           "last_seen_date": self.last_seen_date},
                          f, ensure_ascii=False, indent=2)
        except: pass

    def _check_decay(self):
        today = time.strftime("%Y-%m-%d")
        if self.last_seen_date != today:
            try:
                from datetime import datetime
                days = (datetime.strptime(today, "%Y-%m-%d") -
                        datetime.strptime(self.last_seen_date, "%Y-%m-%d")).days
                if days >= 1: self.affection = max(0, self.affection - min(days, 5))
            except: pass
            self.last_seen_date = today; self._save()

    def update_from_message(self, ui):
        changed = False
        for kw in AFFECTION_POSITIVE:
            if kw in ui: self.affection = min(100, self.affection + 2); changed = True; break
        for kw in AFFECTION_NEGATIVE:
            if kw in ui: self.affection = max(0, self.affection - 4); changed = True; break
        ne = None
        if any(kw in ui for kw in ["哈哈", "开心", "太棒", "好耶", "喜欢", "爱"]): ne = "开心"
        elif any(kw in ui for kw in ["难过", "伤心", "唉", "不开心"]): ne = "关切"
        elif any(kw in ui for kw in ["讨厌", "滚", "烦"]): ne = "失落"
        if ne: self.current_emotion = ne; self.emotion_until = time.time() + 180; changed = True
        if changed: self._save()

    def tick(self):
        if self.current_emotion != "平静" and time.time() > self.emotion_until:
            self.current_emotion = "平静"; self._save()

    def get_context(self):
        if self.affection >= 85: lv = "非常亲密。"
        elif self.affection >= 70: lv = "亲密。"
        elif self.affection >= 50: lv = "友好。"
        elif self.affection >= 30: lv = "客气。"
        else: lv = "冷淡。"
        ed = {"开心": "心情很好，语气轻快。", "关切": "很关心对方。",
              "失落": "有些失落。", "平静": "心情平静。"}.get(self.current_emotion, "")
        return f"\n\n【关系】好感度 {self.affection}/100（{lv}）情绪：{self.current_emotion}（{ed}）"
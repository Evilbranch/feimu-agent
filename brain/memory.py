"""短期记忆 + RAG 向量库（按槽位隔离：owner / friend / audience）"""
import os
import json
import time
import random
import threading
from core.constants import (DATA_DIR, CHROMA_DIR, SHORT_TERM_TURNS,
    RAG_TOP_K, RAG_DISTANCE_THRESHOLD, EMBEDDING_MODEL)


# ══════════════════════════════════════════════════════════════
# 槽位定义
# ══════════════════════════════════════════════════════════════
SLOTS = ("owner", "friend", "audience")

_SLOT_FILES = {
    "owner":    os.path.join(DATA_DIR, "chat_history_owner.json"),
    "friend":   os.path.join(DATA_DIR, "chat_history_friend.json"),
    "audience": os.path.join(DATA_DIR, "chat_history_audience.json"),
}

_current_slot = ["owner"]


def set_slot(slot):
    if slot not in SLOTS:
        slot = "owner"
    _current_slot[0] = slot


def get_slot():
    return _current_slot[0]


# ══════════════════════════════════════════════════════════════
# History 读写
# ══════════════════════════════════════════════════════════════
def load_history(slot=None):
    if slot is None:
        slot = _current_slot[0]
    path = _SLOT_FILES.get(slot)
    if not path or not os.path.exists(path):
        # 首次加载 owner 时尝试从旧文件迁移
        if slot == "owner":
            old_path = os.path.join(DATA_DIR, "chat_history.json")
            if os.path.exists(old_path):
                try:
                    with open(old_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data:
                        print(f"[记忆] 从旧文件迁移 {len(data)} 条到 owner 槽")
                        save_history(data, slot="owner")
                        return data
                except:
                    pass
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_history(h, slot=None):
    if slot is None:
        slot = _current_slot[0]
    path = _SLOT_FILES.get(slot)
    if not path:
        return
    h = h[-SHORT_TERM_TURNS * 2:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=2)
    except:
        pass


# ══════════════════════════════════════════════════════════════
# RAG 向量记忆（按槽位隔离 collection）
# ══════════════════════════════════════════════════════════════
class RAGMemory:
    def __init__(self):
        self.client = None
        self.collections = {}
        self.current_slot = "owner"
        self.enabled = False
        self._loaded = False
        self._lock = threading.Lock()
        self._loading_started = False
        self._pending = {s: [] for s in SLOTS}

    def switch_slot(self, slot):
        if slot not in SLOTS:
            return
        if slot == self.current_slot:
            return
        print(f"[RAG] 切换槽位：{self.current_slot} → {slot}")
        self.current_slot = slot

    def start_background_load(self):
        if self._loading_started:
            print("[RAG] 已加载，跳过")
            return
        self._loading_started = True
        threading.Thread(target=self._ensure, daemon=True).start()

    def _ensure(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._do_load()
            self._loaded = True

    def _do_load(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            print("[RAG] 加载嵌入模型...")
            self.client = chromadb.PersistentClient(path=CHROMA_DIR)
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL)
            for slot in SLOTS:
                col_name = f"feimu_{slot}"
                self.collections[slot] = self.client.get_or_create_collection(
                    name=col_name, embedding_function=ef)
            self.enabled = True
            counts = {s: c.count() for s, c in self.collections.items()}
            print(f"[RAG] 加载完成 {counts}")
            for slot, items in self._pending.items():
                for u, a, ts in items:
                    self._do_add(u, a, ts, slot)
            self._pending = {s: [] for s in SLOTS}
        except Exception as e:
            print(f"[RAG] 加载失败：{e}")
            self.enabled = False

    def add(self, ut, at, ts=None, slot=None):
        if slot is None:
            slot = self.current_slot
        if not self._loaded:
            self._pending[slot].append((ut, at, ts))
            return
        if not self.enabled:
            return
        self._do_add(ut, at, ts, slot)

    def _do_add(self, ut, at, ts, slot):
        col = self.collections.get(slot)
        if not col:
            return
        try:
            if ts is None:
                ts = time.time()
            did = f"conv_{int(ts*1000)}_{random.randint(0, 9999)}"
            col.add(
                documents=[f"用户说：{ut}\n绯木回答：{at}"],
                metadatas=[{"time": ts, "user": ut, "ai": at}],
                ids=[did])
        except:
            pass

    def search(self, q, top_k=RAG_TOP_K, slot=None):
        if slot is None:
            slot = self.current_slot
        if not self._loaded or not self.enabled:
            return []
        col = self.collections.get(slot)
        if not col:
            return []
        try:
            res = col.query(query_texts=[q], n_results=top_k)
            ms = []
            if res and res.get("documents"):
                docs = res["documents"][0]
                dists = res.get("distances", [[]])[0]
                for i, doc in enumerate(docs):
                    d = dists[i] if i < len(dists) else 999
                    if d < RAG_DISTANCE_THRESHOLD:
                        ms.append(doc)
            return ms
        except:
            return []

    def stats(self):
        if not self._loaded:
            return {s: "?" for s in SLOTS}
        return {s: c.count() for s, c in self.collections.items()}
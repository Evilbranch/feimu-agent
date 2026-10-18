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
                for u, a, ts, meta in items:
                    self._do_add(u, a, ts, slot, meta)
            self._pending = {s: [] for s in SLOTS}
        except Exception as e:
            print(f"[RAG] 加载失败：{e}")
            self.enabled = False

    def add(self, ut, at, ts=None, slot=None, extra_metadata=None):
        if slot is None:
            slot = self.current_slot
        if not self._loaded:
            self._pending[slot].append((ut, at, ts, extra_metadata))
            return
        if not self.enabled:
            return
        self._do_add(ut, at, ts, slot, extra_metadata)

    def _do_add(self, ut, at, ts, slot, extra_metadata=None):
        col = self.collections.get(slot)
        if not col:
            return
        try:
            if ts is None:
                ts = time.time()
            did = f"conv_{int(ts*1000)}_{random.randint(0, 9999)}"
            meta = {"time": ts, "user": ut, "ai": at}
            
            # ★ 极简实体抽取：利用正则匹配容易识别的专有名词（科幻/游戏/人名）
            # 实际运行中，用正则比调 LLM 快 100 倍，且不耗显存
            _ENTITY_KEYWORDS = ["诺兰", "星际穿越", "汉斯季默", "三体", "我的世界", "Minecraft", "无主之地", "虚拟主播", "Neuro", "哲学", "黑洞"]
            found_entities = [k for k in _ENTITY_KEYWORDS if k in ut or k in at]
            if found_entities:
                meta["entities"] = ",".join(found_entities)
                print(f"[RAG] 抽取到实体: {found_entities}")
            
            if extra_metadata:
                meta.update(extra_metadata)
                
            col.add(
                documents=[f"用户说：{ut}\n绯木回答：{at}"],
                metadatas=[meta],
                ids=[did])
        except Exception as e:
            print(f"[RAG] _do_add 异常: {e}")

    def add_fact(self, fact_key, fact_value, slot="owner"):
        """写入一条事实，并自动失效同类旧事实（P0-1b 双时态）"""
        if not self._loaded or not self.enabled:
            return
        col = self.collections.get(slot)
        if not col:
            return
        
        now = time.time()
        # 1. 查找该 fact_key 的旧事实并失效
        try:
            existing = col.get(where={"fact_key": fact_key})
            old_ids = existing.get("ids", [])
            if old_ids:
                old_metas = existing.get("metadatas", [])
                for i, old_id in enumerate(old_ids):
                    m = old_metas[i] if (old_metas and i < len(old_metas)) else {}
                    if m is None: m = {}
                    
                    # 如果旧事实本来就是过期的，不用更新
                    if m.get("valid_until", 0.0) > 0 and m.get("valid_until", 0.0) <= now:
                        continue
                    m["valid_until"] = now
                    col.update(ids=[old_id], metadatas=[m])
        except Exception as e:
            print(f"[RAG] 查找/更新旧事实异常: {e}")
            
        # 2. 写入新事实
        ts = now
        did = f"fact_{int(ts*1000)}_{random.randint(0, 9999)}"
        try:
            col.add(
                ids=[did],
                documents=[fact_value],
                metadatas=[{
                    "time": ts,
                    "fact_key": fact_key,
                    "valid_from": ts,
                    "valid_until": 0.0,
                    "user": "system_consolidate",
                    "ai": fact_value
                }]
            )
            print(f"[RAG] 写入新事实: [{fact_key}] {fact_value}")
        except Exception as e:
            print(f"[RAG] 写入新事实异常: {e}")

    def search(self, q, top_k=RAG_TOP_K, slot=None):
        if slot is None:
            slot = self.current_slot
        if not self._loaded or not self.enabled:
            return []
        col = self.collections.get(slot)
        if not col:
            return []
        try:
            # 1. 常规语义搜索
            res = col.query(query_texts=[q], n_results=top_k * 2)
            ms = []
            if res and res.get("documents"):
                docs = res["documents"][0]
                dists = res.get("distances", [[]])[0]
                metas = res.get("metadatas", [[]])[0]
                now = time.time()
                for i, doc in enumerate(docs):
                    d = dists[i] if i < len(dists) else 999
                    if d >= RAG_DISTANCE_THRESHOLD:
                        continue
                    
                    meta = metas[i] if (metas and i < len(metas)) else {}
                    if meta is None: meta = {}
                    
                    valid_until = meta.get("valid_until", 0.0)
                    if valid_until and valid_until <= now:
                        continue
                        
                    ms.append(doc)
                    if len(ms) >= top_k:
                        break

            # ★ 2. 实体图谱联想（如果常规搜索中提到了实体，补充召回相关历史）
            _ENTITY_KEYWORDS = ["诺兰", "星际穿越", "汉斯季默", "三体", "我的世界", "Minecraft", "无主之地", "虚拟主播", "Neuro", "哲学", "黑洞"]
            matched_entities = [k for k in _ENTITY_KEYWORDS if k in q]
            
            if matched_entities:
                for ent in matched_entities:
                    try:
                        # 用 where 过滤包含该实体的 metadata
                        # 注意：ChromaDB 对多个字符串的数组支持有限，我们存的是逗号分隔的字符串
                        # 所以这里用 $contains 匹配（需要 ChromaDB >= 0.4.0，你的 1.5.9 支持）
                        ent_res = col.get(where={"entities": {"$contains": ent}}, limit=2)
                        if ent_res and ent_res.get("documents"):
                            for doc in ent_res["documents"]:
                                if doc not in ms:
                                    ms.append(doc)
                                    print(f"[RAG] 实体图谱关联 ({ent}) 命中")
                                    if len(ms) >= top_k + 2: # 允许稍微多返回一点
                                        break
                    except Exception as ee:
                        pass
                        
            return ms[:top_k + 2]
        except Exception as e:
            print(f"[RAG] search 异常: {e}")
            return []

    def get_embedding(self, text):
        """供外部（如 L2）调用，获取文本的向量表示"""
        if not self._loaded or not self.enabled:
            return None
        try:
            # 从任意一个 collection 拿到 embedding_function
            ef = self.collections["owner"]._embedding_function
            if hasattr(ef, "__call__"):
                # SentenceTransformer 支持批量传入
                return ef([text])[0]
        except Exception as e:
            print(f"[RAG] 获取向量失败: {e}")
        return None

    def stats(self):
        if not self._loaded:
            return {s: "?" for s in SLOTS}
        return {s: c.count() for s, c in self.collections.items()}
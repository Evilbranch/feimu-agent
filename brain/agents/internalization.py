"""内化 Agent：把搜索结果变成绯木的认知

职责：
- analyze()：LLM 把原始搜索结果拆成 facts / my_reason / my_view / my_plan / uncertain
- execute()：审计后写入 L1 / L0 / L2 / learning_tasks

与 _do_explore 的分工：
- _do_explore 负责搜索和流程调度
- InternalizationAgent 负责把"信息"变成"认知"

优化（2026-10-14）：
- 优先走 Ollama 原生 API（支持 think=False + keep_alive=24h）
- 非 Ollama 提供商回退到 OpenAI 兼容层
- 搜索结果截断到 1200 字，减少 prefilling 时间
- 支持 model_override，可用小模型加速内化
"""
import json
import re
import time
import uuid

from core import state
from brain import l0_model
from brain import l0_audit


_ANALYZE_SYSTEM = """你是绯木。你刚刚在网上搜索了"{topic}"，搜索结果在下面。

请用你自己的话，回答五个问题。不要照抄搜索结果。

1. 【facts】这个主题的客观事实是什么？1~2 句，不许编造，不许加自己的看法。
2. 【my_reason】我为什么想了解这个？联系哥哥、我们的关系、或我自己的目标。
3. 【my_view】我自己的看法是什么？必须以"我觉得"或"我认为"开头。
4. 【my_plan】我打算怎么用它？具体行动，不是泛泛而谈。
5. 【uncertain】我还不太确定的、想进一步了解的问题列表（可以为空）。

严格要求：
- facts 只写搜索结果里明确有的
- my_view 可以主观，但不能编造"我记得""我以前"
- 如果某个问题搜索结果不够回答，写"我还不清楚"
- 不要写"作为AI""作为助手"

直接输出 JSON，不要任何前后缀：
{{
  "facts": "...",
  "my_reason": "...",
  "my_view": "...",
  "my_plan": "...",
  "uncertain": ["...", "..."]
}}
"""

# 搜索结果截断长度（降低 prefilling 时间）
_RAW_RESULT_LIMIT = 1200


def _is_ollama(provider):
    """判断 provider 是不是 Ollama"""
    if not provider:
        return False
    base = (provider.get("base_url") or "").lower()
    if "11434" in base or "ollama" in base:
        return True
    if provider.get("api_key") == "ollama":
        return True
    return False


def _ollama_native_url(base_url):
    """把 OpenAI 兼容层的 base_url 转成 Ollama 原生 API URL"""
    b = (base_url or "").rstrip("/")
    # 去掉末尾的 /v1
    if b.endswith("/v1"):
        b = b[:-3]
    return b + "/api/chat"


class InternalizationAgent:
    def __init__(self, client, provider, model_override=None):
        """
        client: OpenAI 兼容客户端（用于非 Ollama）
        provider: dict，含 base_url / model / api_key
        model_override: 可选，用更小的模型加速内化
        """
        self.client = client
        self.provider = provider or {}
        self.model = model_override or self.provider.get("model", "")

    # ══════════════════════════════════════════════════
    # 底层调用：优先 Ollama 原生，否则走 OpenAI
    # ══════════════════════════════════════════════════
    def _call_llm(self, system_prompt, user_content, timeout=120):
        """返回 LLM 生成的文本；失败返回空字符串"""
        # ── Ollama 原生 API ──
        if _is_ollama(self.provider):
            try:
                import requests
                url = _ollama_native_url(self.provider.get("base_url"))
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "stream": False,
                    "think": False,        # 关闭 thinking，节省时间
                    "keep_alive": "24h",   # 常驻 24 小时
                    "options": {
                        "temperature": 0.6,
                        "num_predict": 400,
                        "num_ctx": 4096,
                    },
                }
                r = requests.post(url, json=payload, timeout=timeout)
                r.raise_for_status()
                data = r.json()
                text = (data.get("message") or {}).get("content", "")
                return (text or "").strip()
            except Exception as e:
                print(f"[内化] Ollama 原生调用失败: {e}，回退 OpenAI 兼容层")

        # ── 回退：OpenAI 兼容层 ──
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                timeout=timeout,
                temperature=0.6,
                max_tokens=400,
                extra_body={"keep_alive": "24h", "think": False},
            )
            text = (r.choices[0].message.content or "").strip()
            return text
        except Exception as e:
            print(f"[内化] OpenAI 兼容层调用失败: {e}")
            return ""

    # ══════════════════════════════════════════════════
    # 分析：搜索结果 → 结构化认知
    # ══════════════════════════════════════════════════
    def analyze(self, topic, raw_result, context=None):
        """输入搜索结果，输出结构化认知

        返回 dict：
        {
            "facts": "...",
            "my_reason": "...",
            "my_view": "...",
            "my_plan": "...",
            "uncertain": [...],
            "topic": topic,
        }
        失败返回 None
        """
        if not topic or not raw_result:
            return None

        system_prompt = _ANALYZE_SYSTEM.format(topic=topic)

        # 截断搜索结果，降低 prefilling
        raw_truncated = raw_result[:_RAW_RESULT_LIMIT]
        user_content = f"搜索结果：\n{raw_truncated}"

        # 可选上下文
        if context:
            ctx_lines = []
            if context.get("emotion_label"):
                ctx_lines.append(f"我此刻情绪：{context['emotion_label']}")
            if context.get("recent_topics"):
                ctx_lines.append(
                    f"我最近在关注：{', '.join(context['recent_topics'][:3])}"
                )
            if ctx_lines:
                user_content = "\n".join(ctx_lines) + "\n\n" + user_content

        t0 = time.time()
        text = self._call_llm(system_prompt, user_content)
        elapsed = time.time() - t0

        if not text:
            print(f"[内化] LLM 无输出（{elapsed:.1f}s）")
            return None

        # 去 markdown 包裹
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            print(f"[内化] 无 JSON 边界（{elapsed:.1f}s）: {text[:120]}")
            return None

        try:
            data = json.loads(text[start:end + 1])
        except Exception as e:
            print(f"[内化] JSON 解析失败（{elapsed:.1f}s）: {e}")
            return None

        data["topic"] = topic
        print(f"[内化] analyze 完成（{elapsed:.1f}s）")
        return data

    # ══════════════════════════════════════════════════
    # 执行：审计 + 写入 L1/L0/L2/learning_tasks
    # ══════════════════════════════════════════════════
    def execute(self, analysis):
        """把结构化认知写入各层

        返回 dict：
        {
            "written": True/False,
            "ep_id": "...",
            "l0_action": "created/merged/updated/...",
            "new_tasks": [...],
            "issues": [...],
        }
        """
        result = {
            "written": False,
            "ep_id": None,
            "l0_action": None,
            "new_tasks": [],
            "issues": [],
        }

        if not analysis:
            return result

        topic = analysis.get("topic", "")
        facts = (analysis.get("facts") or "").strip()
        my_reason = (analysis.get("my_reason") or "").strip()
        my_view = (analysis.get("my_view") or "").strip()
        my_plan = (analysis.get("my_plan") or "").strip()
        uncertain = analysis.get("uncertain") or []

        if not topic or not facts:
            result["issues"].append("topic 或 facts 为空")
            return result

        # ═══════════════════════════════════════
        # 1. 审计 my_view
        # ═══════════════════════════════════════
        evidence_id = f"search_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        evidence_ids = [evidence_id]

        if my_view:
            ok, cleaned, issues = l0_audit.audit_opinion(
                topic, my_view, confidence=0.5, evidence_ids=evidence_ids
            )
            result["issues"].extend(issues)
            if not ok:
                print(f"[内化] 观点被拒: {issues}")
                my_view = None  # 观点不写，事实照写
            else:
                my_view = cleaned["view"]

        # ═══════════════════════════════════════
        # 2. 写入 L1 语义记忆
        # ═══════════════════════════════════════
        s = state.get_state()
        if s.rag and s.rag._loaded:
            try:
                s.rag.add(
                    f"[我主动了解] {topic}",
                    facts,
                    slot="owner",
                )
                print(f"[内化] L1 写入: {topic} → {facts[:40]}")
            except Exception as e:
                result["issues"].append(f"L1 写入失败: {e}")
        else:
            result["issues"].append("RAG 未就绪，跳过 L1")

        # ═══════════════════════════════════════
        # 3. 写入 L0 观点
        # ═══════════════════════════════════════
        if my_view:
            try:
                action, opinion = l0_model.upsert_opinion(
                    topic=topic,
                    new_view=my_view,
                    confidence=0.5,
                    evidence_ids=evidence_ids,
                    source="internalization",
                    force=bool(analysis.get("_force", False)),
                )
                result["l0_action"] = action
                print(f"[内化] L0 {action}: {topic}")

                # 把 my_reason / my_plan 也记进 opinion
                if opinion is not None:
                    # ★ 用 upsert 返回的 opinion 里真实的 topic（可能被合并过）
                    actual_topic = opinion.get("topic", topic)
                    if my_plan:
                        opinion["my_plan"] = my_plan
                    if my_reason:
                        opinion["my_reason"] = my_reason
                    try:
                        m = l0_model.load_self_model()
                        m["opinions"][actual_topic] = opinion
                        l0_model.save_self_model(m, snapshot=False)
                    except Exception:
                        pass
            except Exception as e:
                result["issues"].append(f"L0 写入失败: {e}")

        # ═══════════════════════════════════════
        # 4. 写入 L2 情景记忆
        # ═══════════════════════════════════════
        try:
            from brain.episodic import record_episode
            from brain.persona import get_current_state

            st = get_current_state()
            ep_id = record_episode(
                content=f"我主动了解了「{topic}」",
                event_type="discovery",
                entities=[topic],
                self_emotion=st.get("emotion"),
                self_intent="explore",
                self_role="learner",
                channel="inner",
                raw_context=(
                    f"搜索词：{topic}\n"
                    f"facts：{facts[:150]}\n"
                    f"my_view：{(my_view or '（无）')[:150]}"
                ),
            )
            result["ep_id"] = ep_id
            print(f"[内化] L2 写入: {ep_id}")
        except Exception as e:
            result["issues"].append(f"L2 写入失败: {e}")

        # ═══════════════════════════════════════
        # 5. uncertain → learning_tasks
        # ═══════════════════════════════════════
        for q in uncertain:
            q = (q or "").strip()
            if not q or len(q) < 3:
                continue
            try:
                tid = l0_model.add_learning_task(
                    topic=q[:80],
                    reason=f"探索「{topic}」时产生的疑问",
                    priority=0.5,
                    source="internalization",
                )
                if tid:
                    result["new_tasks"].append(tid)
            except Exception as e:
                result["issues"].append(f"learning_task 写入失败: {e}")

        result["written"] = True
        return result
"""认知编排器：决定唤醒后做什么

职责：
- 读 context（learning_tasks / open_questions / L0 变化）
- 决定这次唤醒走"认知任务"还是"常规决策"
- 认知任务由本模块执行；常规决策交给 inner_life 原逻辑

设计原则：
- 不抢 speak 的活（主动说话仍走 inner_life._ask_intent）
- 只做"目标驱动"的任务路由
- 每次唤醒最多执行 1 个任务
"""
import time
from datetime import datetime

from brain import l0_model


# 调度参数
QUESTION_STALE_DAYS = 3     # open_question 超过 N 天未动 → reflect
DEFAULT_NEXT_WAKE = 1800    # 执行完任务后多久再醒
MAX_TASKS_PER_WAKE = 1      # 每次唤醒最多执行任务数


class CognitiveOrchestrator:
    def __init__(self):
        pass

    # ══════════════════════════════════════════════════
    # 路由：决定本次做什么
    # ══════════════════════════════════════════════════
    def route(self):
        """返回 dict：
        {
            "task_type": "explore | reflect | internalize | consolidate | normal",
            "reason": "...",
            "payload": {...},
            "next_wake": int,
        }
        "normal" 表示没有认知任务，交回 inner_life 原逻辑
        """

        # ── 优先级 1：pending learning_task ──
        try:
            tasks = l0_model.list_learning_tasks("pending")
            if tasks:
                # 按 priority 降序，取最高
                tasks_sorted = sorted(
                    tasks, key=lambda t: -float(t.get("priority", 0))
                )
                task = tasks_sorted[0]
                topic = task.get("topic", "").strip()
                if topic:
                    return {
                        "task_type": "explore",
                        "reason": f"学习任务：{topic}",
                        "payload": {
                            "task_id": task.get("id"),
                            "topic": topic,
                        },
                        "next_wake": DEFAULT_NEXT_WAKE,
                    }
        except Exception as e:
            print(f"[编排] learning_task 读取失败: {e}")

        # ── 优先级 2：stale open_question ──
        try:
            questions = l0_model.list_open_questions("exploring")
            now = time.time()
            for q in questions:
                created = q.get("created_at")
                if not created:
                    continue
                try:
                    ts = datetime.strptime(
                        created, "%Y-%m-%dT%H:%M:%S"
                    ).timestamp()
                except Exception:
                    continue
                age_days = (now - ts) / 86400
                if age_days >= QUESTION_STALE_DAYS:
                    return {
                        "task_type": "reflect",
                        "reason": (
                            f"未解问题已 {age_days:.0f} 天："
                            f"{q.get('question', '')[:40]}"
                        ),
                        "payload": {
                            "question_id": q.get("id"),
                            "question": q.get("question", ""),
                        },
                        "next_wake": DEFAULT_NEXT_WAKE,
                    }
        except Exception as e:
            print(f"[编排] open_question 读取失败: {e}")

        # ── 无认知任务 ──
        return {
            "task_type": "normal",
            "reason": "无认知任务，走常规决策",
            "payload": {},
            "next_wake": None,
        }

    # ══════════════════════════════════════════════════
    # 执行：把路由结果落地
    # ══════════════════════════════════════════════════
    def execute(self, route, client, provider):
        """执行认知任务。返回 True/False"""
        task_type = route.get("task_type", "normal")
        payload = route.get("payload", {})

        if task_type == "explore":
            return self._exec_explore(payload, client, provider)
        elif task_type == "reflect":
            return self._exec_reflect(payload, client, provider)
        return False

    # ── explore ──
    def _exec_explore(self, payload, client, provider):
        topic = payload.get("topic", "").strip()
        task_id = payload.get("task_id")
        if not topic:
            return False

        import threading
        try:
            from brain.inner_life import _do_explore
        except Exception as e:
            print(f"[编排] 无法导入 _do_explore: {e}")
            return False

        print(f"[编排] 执行 explore: {topic}（异步）")
        def _bg():
            try:
                _do_explore(topic, client, provider)
            except Exception as e:
                print(f"[编排] explore 失败: {e}")
        threading.Thread(target=_bg, daemon=True).start()

        # 标记任务完成
        if task_id:
            try:
                l0_model.complete_learning_task(task_id, result="executed")
            except Exception as e:
                print(f"[编排] 标记任务完成失败: {e}")
        return True

    # ── reflect ──
    def _exec_reflect(self, payload, client, provider):
        """对 stale question 做一次反思

        v1 简化：用 LLM 结合现有 L2 给出一个尝试性回答，
        写入 opinion 的 hypotheses。
        """
        qid = payload.get("question_id")
        question = payload.get("question", "")
        if not qid or not question:
            return False

        print(f"[编排] 执行 reflect: {question[:50]}")

        # 暂时只记录到 open_question 的 last_progress
        # 完整反思留到后续阶段
        try:
            m = l0_model.load_self_model()
            for q in m.get("open_questions", []):
                if q.get("id") == qid:
                    q["last_progress"] = datetime.now().strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    q.setdefault("hypotheses", []).append(
                        f"（{datetime.now().strftime('%m-%d')} 反思："
                        f"待深化，暂无新证据）"
                    )
                    break
            l0_model.save_self_model(m, snapshot=False)
            return True
        except Exception as e:
            print(f"[编排] reflect 写入失败: {e}")
            return False
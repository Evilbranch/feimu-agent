"""补录历史对话到 L2 情景记忆

用途：微信端 L2 写入缺失时，从 full_history.log 回溯补录
用法：python tools/backfill_episodes.py
"""
import os
import re
import sys
import json
import time
from openai import OpenAI

# 加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from brain.episodic import judge_should_record, record_episode
from brain.persona import get_current_state

LOG_FILE = os.path.join(SCRIPT_DIR, "data", "full_history.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "data", "config.json")


def load_history():
    """解析 full_history.log，返回 [(ts, source, user, reply), ...]"""
    if not os.path.exists(LOG_FILE):
        print(f"[补录] 找不到 {LOG_FILE}")
        return []

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 按 === [时间] source=xxx === 分块
    blocks = re.split(r"=== \[([^\]]+)\] source=(\w+) ===", content)
    # blocks: ['', '时间1', 'source1', 'body1', '时间2', 'source2', 'body2', ...]

    items = []
    for i in range(1, len(blocks) - 2, 3):
        try:
            ts_str = blocks[i].strip()
            src = blocks[i + 1].strip()
            body = blocks[i + 2].strip()

            # 解析 body: "用户：xxx\n绯木：xxx"
            m_user = re.search(r"用户：(.*?)(?:\n绯木：|$)", body, re.DOTALL)
            m_rep = re.search(r"绯木：(.*?)$", body, re.DOTALL)
            if not m_user or not m_rep:
                continue

            user_msg = m_user.group(1).strip()
            reply = m_rep.group(1).strip()

            # 解析时间戳
            try:
                t = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                t = time.time()

            if user_msg and reply:
                items.append((t, src, user_msg, reply))
        except Exception:
            continue

    return items


def main():
    # 加载配置
    if not os.path.exists(CONFIG_FILE):
        print(f"[补录] 找不到 {CONFIG_FILE}")
        return
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    provider_key = cfg.get("current_provider")
    provider = cfg["providers"][provider_key]
    client = OpenAI(api_key=provider["api_key"], base_url=provider["base_url"])

    items = load_history()
    print(f"[补录] 共找到 {len(items)} 轮对话")

    recorded = 0
    skipped = 0
    failed = 0

    for i, (ts, src, user_msg, reply) in enumerate(items):
        ts_str = time.strftime("%m-%d %H:%M", time.localtime(ts))
        print(f"\n[{i+1}/{len(items)}] {ts_str} [{src}]")
        print(f"  用户：{user_msg[:50]}")
        print(f"  绯木：{reply[:50]}")

        try:
            should, etype, content, imp = judge_should_record(
                client, provider, user_msg, reply
            )
            if not should or not content:
                print(f"  → 不记录")
                skipped += 1
                continue

            # 用原始时间戳写入
            ep_id = record_episode(
                content=content,
                event_type=etype,
                entities=[],
                self_emotion={"label": "平静", "valence": 0.2, "arousal": 0.4},
                self_intent="speak",
                self_role="responder",
                channel=src,
                raw_context=f"哥哥：{user_msg}\n我：{reply}",
                novelty=imp,
                ts=ts,
            )
            if ep_id:
                print(f"  → 记录: type={etype} | {content[:40]}")
                recorded += 1
            else:
                failed += 1

        except Exception as e:
            print(f"  → 异常: {e}")
            failed += 1

        # 轻微延迟，避免 LLM 排队
        time.sleep(0.3)

    print(f"\n[补录完成] 记录 {recorded} 条 / 跳过 {skipped} 条 / 失败 {failed} 条")


if __name__ == "__main__":
    main()
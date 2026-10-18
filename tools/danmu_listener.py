import asyncio
import time
import aiohttp
from bilibili_api import live

# ================= 配置区 =================
ROOM_ID = 1789070678           # 你的直播间真实ID
API_URL = "http://127.0.0.1:8765/danmu" # 绯木主程序接收弹幕的接口
COOLDOWN = 15                  # 限流：处理完一条弹幕后，至少等 15 秒再处理下一条
KEYWORDS = ["绯木", "木木", "主播", "妹妹"] # 必须包含这些词才会被选中
# ==========================================

_last_time = 0

async def main():
    global _last_time
    room = live.LiveDanmaku(ROOM_ID)

    @room.on("DANMU_MSG")
    async def on_danmaku(event):
        global _last_time
        
        # 1. 提取信息
        info = event["data"]["info"]
        uname = info[2][1]          # 用户名
        content = info[1]           # 弹幕内容
        
        # 2. 过滤：不需要她回应的弹幕（比如纯表情、太短的、没有叫她的）
        if not any(k in content for k in KEYWORDS):
            return
            
        # 3. 限流：如果距离上次说话不到 15 秒，直接忽略，防止弹幕刷屏把她逼疯
        now = time.time()
        if now - _last_time < COOLDOWN:
            # print(f"[弹幕] 冷却中，忽略: {content}")
            return
            
        _last_time = now
        print(f"\n[弹幕命中] {uname}: {content}")

        # 4. 发送给绯木的主程序
        payload = {
            "uname": uname,
            "text": content
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(API_URL, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        print(f"[绯木回复]: {result.get('reply', '（无回复）')}")
                    else:
                        print(f"[弹幕] 转发失败: HTTP {resp.status}")
        except Exception as e:
            print(f"[弹幕] 无法连接主程序 (请确保 my_ai.py 已启动): {e}")

    print(f"🎤 弹幕监听已启动，当前房间: {ROOM_ID}")
    print(f"🔍 只有包含 {KEYWORDS} 的弹幕才会被处理，且 {COOLDOWN} 秒内只回一条。")
    await room.connect()

if __name__ == "__main__":
    asyncio.run(main())
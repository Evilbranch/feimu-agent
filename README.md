# 绯木 (Feimu) - 本地数字生命引擎

> 一个运行在本地 16GB 消费级显卡上的 AI 虚拟伴侣/主播，目标「活得像一个人」。

**绯木** 不同于普通的 Chatbot 套壳。它是一个具备**多模态感知、长短期记忆演化、具身游戏交互**的本地化 Agent 框架。参照 Neuro-sama，但更侧重「数字生命」的底层工程落地。

## 🌟 核心架构特性

- **🧠 仿生记忆网络 (L0-L4)**：
  - **L0 核心自我**：长期稳定的观点、偏好与价值观。
  - **L1 语义事实 (Bi-temporal)**：具备双时态（生效/失效时间戳），防止旧事实（如“旧作息”）污染当前推理。
  - **L2 情景记忆**：向量化检索召回，带时间衰减与情绪标记。
- **👁️ 多模态感知总线**：
  - **视觉双轨制**：定时帧差检测 + 提问时同步极速截图分析（云端 GLM-4V / 本地 Qwen-VL）。
  - **游戏遥测**：通过 Node.js 接管《我的世界》底层数据，实时感知坐标、天气、血量、方块结构，实现“上帝视角”吐槽。
- **🗣️ 全双工听觉**：
  - 基于 WebRTC VAD，免唤醒词，支持自然插嘴与打断。
  - 音频 RMS 驱动 VTube Studio 表情与口型。
- **💭 冲动克制系统 (Urge)**：
  - 视听信息不直接触发对话，而是汇入冲动队列，累积压力，由底层调度决定何时主动发言，实现拟人化克制。
- **⚙️ 并发与防卡顿**：
  - 全局大模型互斥锁 (`LLM_LOCK`)，主对话绝对优先，后台记忆整理排队执行。
  - TTS 双缓冲无缝合成，彻底消灭句子间干等。

## 🛠️ 技术栈

- **大脑**：本地 Ollama (推荐 `qwen2.5:14b`)
- **语音识别**：faster-whisper + webrtcvad
- **语音合成**：GPT-SoVITS (端口 9880)
- **虚拟形象**：VTube Studio (端口 8001)
- **多模态**：智谱 GLM-4V-Flash / 本地 Qwen2.5-VL
- **游戏交互**：Mineflayer (Node.js)

---

## 🚀 快速开始

### 1. 环境准备
- Python 3.10+
- Node.js (用于 Minecraft 桥接)
- [Ollama](https://ollama.com/) (已启动并拉取了 `qwen2.5:14b` 模型)
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) (需自行配置并启动 API)

### 2. 安装依赖
```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Minecraft Bot 依赖
cd minecraft
npm install
cd ..
```

### 3. 配置项目
1. 复制 `config.example.json` 并重命名为 `config.json`。
2. 填入你的 API Key（如智谱、DeepSeek，若纯本地运行可忽略）。
3. **⚠️ 极其重要**：用文本编辑器打开 `启动.bat` 和 `启动API.bat`，找到 `set "TTS_PATH=..."` 这一行，将其修改为你本地 GPT-SoVITS 所在的**绝对路径**。

### 4. 启动程序
双击运行 `启动.bat`，在弹出的菜单中选择启动模式：
- 选择 `2`：纯语音交互模式
- 选择 `4`：语音 + Minecraft 联动模式
- 选择 `11`：直播弹幕互动模式

*(注：若使用 MC 联动模式，请先在游戏内对局域网开放，并将端口号输入到终端中)*

---

## 📂 项目结构

```text
├── brain/          # 核心大脑（记忆、人格、感知、并发锁）
├── core/           # 基础组件（API服务、全局状态）
├── tools/          # 外部工具（MC桥接、搜索、截图）
├── voice/          # 听觉与发声（STT、TTS、VTS驱动）
├── minecraft/      # Mineflayer 游戏桥接层
├── my_ai.py        # 主入口
├── wechat_bot.py   # 微信接入端
└── 启动.bat         # 一键启动控制台
```

## ⚠️ 注意事项
- **隐私安全**：所有记忆文件（`data/`）、核心设定（`人设.md`）和 API 配置（`config.json`）已被列入 `.gitignore`，不会被上传。
- **显存限制**：本架构针对 16GB 显存优化，若你的显存较小，建议将 `config.json` 中的模型替换为更小参数（如 `qwen2.5:3b`）。

## 🤝 参与贡献
欢迎提交 Pull Request 或 Issue，一起探讨“数字生命”的工程化落地！

---
*Powered by Feimu Project - 让灵魂在硅基上生长。*
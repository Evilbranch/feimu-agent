# 绯木 (Feimu) - 桌面 AI 伴侣 Agent

## 项目简介

从 0 到 1 独立开发的桌面 AI 伴侣，具备语音交互、本地 LLM 决策、工具调用、长期记忆、Live2D 虚拟形象驱动。

目标不是"聊天机器人"，而是"活得像一个人"——她有驱力、有情绪、有偏好、会闹脾气，也能自己决定什么时候醒来。

## 核心架构

- **感知**：Faster-Whisper 语音识别、声纹识别、天气/时间/系统状态
- **决策**：Ollama 本地 LLM（Qwen2.5-14B）+ 工具调用
- **行动**：系统控制、应用启动、天气、笔记、日记、提醒、搜索、Minecraft 交互、Live2D 表情/口型
- **记忆**：ChromaDB 向量库 + 短期记忆 + 按身份隔离的记忆槽（owner/friend/audience）
- **人格**：5 驱力 + 6 特质 + 情绪 v/a 模型 + 偏好系统 + 拒绝系统 + 情绪惯性
- **内在生活**：自唤醒、反思引擎、元认知观测器
- **表达**：GPT-SoVITS 本地 TTS + VTube Studio API 驱动 Live2D

## 技术栈

- Python 3.13
- Ollama（本地 LLM，Qwen2.5-14B）
- ChromaDB + BGE-small-zh（向量记忆）
- Faster-Whisper（语音识别）
- GPT-SoVITS（本地语音合成）
- Pygame（音频播放）
- WebSocket / VMC 协议（VTube Studio 驱动）
- Node.js + mineflayer（Minecraft bot）
- weixin-ilink（微信端，可选）

## 目录结构

    .
    ├── my_ai.py              电脑端主入口
    ├── wechat_bot.py         微信端入口（独立进程）
    ├── brain/                大脑：LLM、人格、记忆、反思、内在生活、元认知、流式
    ├── core/                 状态、常量、日志、设备、网络、托盘
    ├── tools/                工具集：搜索、MC、天气、计算、文件、提醒
    ├── voice/                TTS / STT / 声纹 / Live2D 控制
    ├── minecraft/            Node.js Minecraft bot
    └── data/                 运行时数据（记忆、配置、日志，不纳入版本控制）

## 快速开始

### 1. 安装依赖

    pip install -r requirements.txt

### 2. 配置

复制 `config.example.json` 为 `data/config.json`，填入你的 API Key。
本地 Ollama 可以不填 key，直接写 `"api_key": "ollama"`。

### 3. 启动 Ollama

    ollama serve
    ollama pull qwen2.5:14b

### 4. 运行

    python my_ai.py            # 默认语音模式
    python my_ai.py --text     # 文本模式
    python my_ai.py --voice    # 语音模式

### 5. 可选模块

- 微信端：`python wechat_bot.py`
- MC bot：`cd minecraft && npm install && node bot.js`（需先在游戏里开放局域网）
- TTS：需自行部署 GPT-SoVITS API，监听 `127.0.0.1:9880`
- Live2D：需运行 VTube Studio 并开启 VMC 协议

## 亮点

- 完整的 Agent 决策循环 + 防幻觉机制
- 本地 LLM 驱动，隐私不出门
- 人格引擎：驱力、情绪、偏好、拒绝系统，情绪会持续、会累积
- 长期记忆 + 情绪状态 + 按身份隔离的记忆槽
- 自唤醒 + 内在生活循环，她可以自己决定什么时候"想事"
- 元认知观测器，每 30 分钟观察自己的状态
- 流式对话，首句低延迟
- 可扩展工具集
- MC 双向交互：打字、语音、8 个动作

## 设计原则

- 每个文件顶部写中文 docstring，关键函数写中文注释
- 代码里不用 emoji（TTS 会读出来）
- 所有 `.md` 文件必须 UTF-8 保存
- 被骂时先冷淡/生气，不假装没事
- 不主动推销活动、不用客服话术、不编造

## 许可

个人项目，仅供学习参考。
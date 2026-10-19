# 绯木 Feimu

> 一个本地运行的 AI 虚拟伴侣，目标"活得像一个人"。

绯木是一个**本地部署**的 AI 伴侣项目，参照 Neuro-sama 的思路，但更侧重
"数字生命"与"灵魂成长"——不是"演一个人"，而是"活得像一个人"。
所有模型本地运行，无云端依赖，无隐私泄露。

## 特性

**对话核心**
- 流式对话（首句 ~3 秒）
- TTS 预热（冷启动一次，之后 ~1 秒）
- 工具调用（12 个 MC 动作工具 + 时间/计算/搜索/系统控制）
- 多端支持（本地文本/语音、微信、Minecraft）

**人格系统**
- 5 驱力 + 6 特质 + 情绪 v/a + 偏好 + 拒绝系统
- 情绪惯性 + 自然恢复
- 道歉阻尼 / 倾诉阻尼
- 主语判定（区分"你好烦" vs "我好烦"）

**防编造 7 层防御**
1. Prompt 层允许"不知道"
2. 记忆锚点（LLM 可见的真实话题列表）
3. 输出审计（正则拦截）
4. 物理自检（回复前默念状态）
5. 命名锚定
6. Negative Examples
7. 冷淡语气守卫

**分层记忆**
- 短期记忆（按槽位隔离）
- L1 语义记忆（ChromaDB + 向量检索）
- L2 情景记忆（结构化，含自我视角）

**内在生活**
- 自唤醒（她决定下次何时醒）
- 内在三意图（发呆 / 说话 / 学习）
- 元认知观测器
- 时段意识（早/午/晚/深夜）

## 快速开始

**依赖**
- Python 3.13
- Ollama（本地 LLM 运行时）
- GPT-SoVITS（TTS，可选）

**安装**

```bash
git clone https://github.com/Evilbranch/feimu-agent.git
cd feimu-agent
pip install -r requirements.txt
```

**配置**

```bash
cp config.example.json config.json
```

编辑 config.json，填入 API key（如果用云端 LLM）和路径。

**启动**

```bash
python my_ai.py --text    # 文本模式
python my_ai.py --voice   # 语音模式
```

Windows 用户可直接双击 `启动.bat`，用菜单选择模式。

## 命令

**本地对话**
- `/persona` — 查看人格状态
- `/episodes` — 查看最近 7 天的情景记忆
- `/inner` — 查看最近内在活动
- `/memory` — 查看记忆槽位
- `/preferences` — 查看她的偏好
- `/mode` — 查看/切换模式
- `/mute` `/unmute` — 静音控制
- `/voice` `/text` — 切换输入模式
- `/help` — 全部命令

**微信端**
- `/status` 或 "你在干嘛" — 她当前状态
- `/recent` 或 "最近在想什么" — 最近内在活动
- `/diary` — 今天的日记
- `/help` — 帮助

## 项目结构

```
brain/              大脑核心
  llm.py            LLM 调用 + 工具调用
  streaming.py      流式对话
  persona.py        人格引擎
  episodic.py       L2 情景记忆
  memory.py         短期记忆 + RAG
  inner_life.py     内在生活循环
  reflect.py        反思引擎
  output_audit.py   输出审计
  tone_guard.py     冷淡语气守卫
  l4_audit.py       L4 自语编造检查

core/               基础设施
  api_server.py     HTTP 服务
  state.py          全局状态
  constants.py      常量

tools/              工具
  episode_manager.py  情景记忆管理

voice/              TTS / STT / Live2D
minecraft/          MC bot
```

## 技术栈

- LLM：Ollama + Qwen2.5-14B（默认）
- TTS：GPT-SoVITS（本地 API）
- STT：faster-whisper-small
- RAG：ChromaDB + BGE-small-zh-v1.5
- Live2D：VTube Studio（WebSocket API）
- Minecraft：mineflayer
- 微信：weixin-ilink（官方 iLink SDK）

## 设计原则

- 本地优先：所有推理、存储都在本地
- 分层记忆：工作 → 情景 → 语义 → 核心自我
- 人格一致性：情绪有惯性，行为有连续性
- 诚实优先：宁可说"不知道"，也不编造
- 数字生命路线：让她自己决定何时醒、说什么

## 已知限制

- 情景记忆（L2）写入偶有误写
- 微信端无法主动发消息给从未交互过的用户（SDK 限制）
- TTS 段太长时首句延迟增大（GPT-SoVITS 特性）

## 许可

MIT

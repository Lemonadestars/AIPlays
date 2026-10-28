# AIPlays：让 AI 自己玩博德之门 3

注：只有本三行由人类撰写，以下内容（包括这个项目本身）几乎是99%的Vibe Coding产物。本项目的绝大部分内容不在作者本身的能力范围内，更新也并不固定。欢迎自行提交Issue、修改并改进代码。
**你需要安装OBS并且按步骤正确配置才能听到语音**
**不要，不要，不要使用本mod进行商业直播的同时声称本mod是100%你自己撰写的。**

[English](README.en.md)

让 Claude、GPT、Gemini、DeepSeek、Grok、Qwen 等 AI **各自扮演一个队友**，自己打仗、自己对话、自己掷骰子，
还会用语音和双语字幕实时吐槽、互相拌嘴。你可以只当观众，也可以亲自操控一个角色和它们组队。适合录视频和直播。

- **战斗**：轮到 AI 的角色时，它看战场（敌我位置、血量、技能射程、战斗日志）自己行动，边打边解说
- **剧情对话**：NPC 说话时 AI 安静听着；出现选项时队友们先七嘴八舌发表意见（“队伍议会”），再由说话的那个角色拍板
- **掷骰子**：检定时 AI 自己掷，天然 20 欢呼、天然 1 互相嘲笑，失败了还会决定要不要用激励重掷
- **长期记忆**：剧情真相、重要选择、好笑的名场面都会记下来，以后讨论剧情、吐槽时会翻旧账
- **语音 + 字幕**：每个 AI 有自己的声音和字幕颜色，OBS 加一个浏览器源就能显示
- **你当导演**：AI 卡关时，在“导演提示”小窗口写一句话点拨，它们会当成自己想到的

> 这是一个粉丝自制的非官方 mod，与 Larian Studios 无关。还在开发中，遇到问题欢迎提 Issue。

---

## 你需要准备什么

| 必需 | 说明 |
|---|---|
| Windows 10 / 11 | |
| 博德之门 3（Steam 或 GOG） | 安装工具会自动找到游戏位置 |
| 至少一个 AI 的 API key | **推荐先申请 DeepSeek**：便宜、中文好。见下面的“API key 去哪申请” |

| 可选 | 说明 |
|---|---|
| MiniMax 的 API key | 情感很丰富的中文语音。不填就用免费的微软语音（edge-tts） |
| OBS | 录制 / 直播时显示字幕、播放 AI 的语音 |

Python、Script Extender 这些**安装工具会自动帮你装**，不用自己找。

---

## 安装（两种方法任选）

### 方法 A：一行命令（最省事）

按 `Win + X` → 选“终端”或“Windows PowerShell”，粘贴下面这行，回车：

```powershell
irm https://raw.githubusercontent.com/Lemonadestars/AIPlays/main/install.ps1 | iex
```

它会下载最新版、问你装到哪个文件夹、在桌面放一个快捷方式，然后打开安装菜单。

### 方法 B：下载 ZIP

点本页上方绿色的 **Code → Download ZIP**，解压到任意文件夹（路径里最好不要有空格以外的特殊符号），
双击里面的 **`AIPlays.bat`**。

---

## 第一次使用：按菜单顺序走一遍

双击 `AIPlays.bat`（或桌面快捷方式）会出现这个菜单：

```
  1. 一键安装 / 修复（Python、依赖、Script Extender、mod）
  2. 填写 API key
  3. 选择谁来玩哪个角色
  4. 测试 AI 和语音的连接
  5. 启动！（调度器 + 导演提示窗口）
  6. 检查状态
  7. OBS 字幕设置说明
  8. 卸载 mod
  9. 更新到最新版
```

1. **选 1 一键安装**。会自动：安装 Python（没有的话）→ 安装依赖 → 找到游戏 → 安装 Script Extender → 安装并启用 mod。
   - 安装 mod 时**游戏必须是关着的**。
   - 如果提示“还没有 modsettings.lsx”：先启动一次游戏到主菜单再退出，然后重新选 1。
2. **选 2 填 API key**。粘贴 key 时屏幕上不显示字符（防止直播泄露），粘贴完直接回车即可。没有的直接回车跳过。
3. **选 3 分配角色**（可跳过，用默认阵容）。没填 key 的 AI 的角色会自动变成由你操作，游戏不会卡住。
4. **选 4 测试连接**。每个 AI 发一句很短的话，看能不能连上、要几秒。
5. **选 5 启动**。会打开一个黑色的“调度器”窗口（别关）和一个导演提示小窗口。然后开游戏、读档，轮到 AI 的角色时它们就会自己行动。

---

## 默认阵容

| 角色 | 谁来玩 | 人设 |
|---|---|---|
| 邪念（主角） | Grok | 毒舌叛逆、有点疯，对体内的杀戮冲动又怕又好奇 |
| 影心 | Claude | 爱指点江山、自视甚高，总觉得自己才该当队长 |
| 莱埃泽尔 | GPT | 寡言少语、不服管，专跟 Claude 对着干 |
| 威尔 | Qwen | 中二热血的正义使者，现场给招式起名 |
| 卡菈克 | Gemini | 元气满满、永远冲在最前面 |
| 阿斯代伦 | DeepSeek | 孩子气的吐槽役，爱耍宝 |
| 其他所有角色（盖尔等） | 你 | 你是队长，AI 们会叫你 User |

菜单 3 可以随便改。人设、模型、声音、字幕颜色都在 `director/config.yaml` 里，有中文注释。

---

## API key 去哪申请

在菜单 2 里，每一项输入 `o` 回车会直接打开申请网页。申请后一般要先充一点钱（几美元就能玩很久）。

| AI | 网址 | 备注 |
|---|---|---|
| DeepSeek | https://platform.deepseek.com/api_keys | 最便宜，**推荐第一个申请**；也负责总结长期记忆 |
| Claude | https://console.anthropic.com/settings/keys | |
| GPT | https://platform.openai.com/api-keys | |
| Gemini | https://aistudio.google.com/apikey | |
| Grok | https://console.x.ai | |
| Qwen | 阿里云百炼 / QwenCloud | 默认用国际站。国内账号要把 `config.yaml` 里 qwen 的 `base_url` 改成注释里的国内地址 |
| MiniMax（语音） | https://www.minimax.io | 可选 |

key 保存在 `director/keys.env`，**只在你自己电脑上**，不会上传。千万别把这个文件发给别人，也别在直播时打开它。

---

## 玩的时候

- **导演提示**：AI 卡关（比如不知道要去用某个机关）时，在小窗口里写一句，比如“别管指挥官，去传送器那里”。**写上具体东西的名字**效果最好。按 Esc 清除。
- **暂停 AI**：在导演提示小窗口勾选“暂停 AI”，所有角色立刻交给你操作，取消勾选就恢复。
- **调度器关了也不会卡死**：调度器窗口一关（或者暂停），mod 几秒内就会把正在行动的角色交还给你。
- **清空记忆、重开一把**：删掉 `director/memories.json` 和 `director/story_log.txt`（先关调度器）。
- **你操作的角色**：战斗中轮到你时 mod 不接管；对话中轮到你的角色说话时，选项由你点，AI 们只在旁边发表意见。

### OBS 设置

1. 来源 → `+` → **浏览器**，URL 填 `http://localhost:8765/overlay.html`，宽 1920、高 1080
2. 勾选 **通过 OBS 控制音频**（AI 语音才会进直播 / 录像）
3. 想自己也听到：混音器 → 这个浏览器源 → 高级音频属性 → 音频监听选 **监听并输出**

---

## 大概要花多少钱

每个 AI 每次行动大约消耗几千 token（输入）+ 几百 token（输出），具体看各家价格。
用默认的快速 / 便宜型号，一场普通战斗一般在几美分到几毛钱之间；DeepSeek、Gemini Flash、Qwen 最便宜，
GPT-6.1 Sol 和 Claude Sonnet 相对贵一些。想省钱可以在 `config.yaml` 里把它们换成更便宜的型号。

---

## 常见问题

**菜单 1 装依赖失败**
多半是网络问题。国内可能需要代理，或者给 pip 配置镜像后再选一次 1。

**进游戏后 AI 不动**
1. 调度器窗口开着吗？有没有红字或 `!` 开头的提示？
2. 菜单 6 检查状态，看 mod 和 Script Extender 是不是都正常。
3. 菜单 6 会显示“mod 在游戏里运行过”。如果一直没有，说明 mod 没加载：确认菜单 6 里 Script Extender 显示 [OK]，退出游戏后菜单 1 重新安装。

**某个 AI 一直“掉线了，这回合我先歇着”**
它的 API 调用失败了。菜单 4 测试连接，看具体报错（key 填错、余额不足、型号名过期等）。

**OBS 没字幕、没声音**
确认调度器开着、只开了一个；浏览器源右键 → 属性 → **刷新当前页面的缓存**。用普通浏览器打开 `http://localhost:8765/overlay.html` 能看到页面，就说明是 OBS 设置的问题。

**游戏更新后 mod 失效**
先等 Script Extender 更新（它会在游戏启动时自动更新），再用菜单 9 更新本项目。

**用 BG3 Mod Manager 管理 mod**
也可以：把 `mod/AIPlays.pak` 拖进 Mod Manager 启用即可，菜单 1 的“启用 mod”那步会自动跳过已启用的情况。

---

## 已知限制

- 依赖 Norbyte 的 [BG3 Script Extender](https://github.com/Norbyte/bg3se)。游戏大更新后可能要等它跟进。
- AI 只能指定“单位”或“物体”作为目标，需要瞄准地面的技能（跳跃、扔东西等）暂时不给 AI 用。
- 升级目前是 AI 决定方案、念给观众听，**升级界面由你按方案点**（自动点击还没做）。
- “使用物体”（拉杆、控制台等）和升级检测还在测试中，可能有不灵的情况。
- 只在中文版游戏上测试过。英文版应该也能用，但角色名匹配要在 `config.yaml` 的 `characters` 里加英文名（默认已带主要同伴的英文名）。

---

## 给开发者

```
AIPlays.bat / setup.ps1   安装和启动菜单（PowerShell，兼容 Windows 自带的 5.1）
install.ps1               一行命令安装 / 更新用的引导脚本
mod/AIPlays/              游戏里的 Lua mod 源码（服务端：战斗；客户端：对话、掷骰、战斗日志）
mod/AIPlays.pak           打包好的 mod；改了 Lua 后用 tools/pack_mod.py 重新打包
director/
  director.py             调度器主程序：读游戏状态 → 问 AI → 解说 → 把指令写回游戏
  providers.py            AI 适配层（Anthropic / OpenAI / 兼容 OpenAI 的平台 / mock）
  prompts.py              提示词
  story.py                剧情、队伍议会、掷骰
  memory.py               长期记忆
  banter.py               队友互相吐槽
  levelup.py              升级方案
  narrator.py             语音（MiniMax / edge-tts / Azure / OpenAI）+ OBS 字幕页面
  setup_env.py            keys.env、party.yaml、缺 key 时的保护
  config.yaml             人设、模型、声音、节奏
  fake_game.py / fake_dialog.py   不开游戏也能测试整条链路
tools/pack_mod.py         不依赖第三方库的 .pak 打包工具（LSPK v18）
```

mod 和调度器之间通过 Script Extender 文件夹里的几个 JSON 文件通信（`AIPlays_state.json`、`AIPlays_command.json`、
`AIPlays_dialog.json` 等），mod 的运行日志在 `AIPlays_log.txt`。

不开游戏测试：

```powershell
cd director
bg3-env\Scripts\python.exe director.py --se-dir test_se     # 一个窗口
bg3-env\Scripts\python.exe fake_game.py --se-dir test_se    # 另一个窗口
```

## 致谢

- [Norbyte / BG3 Script Extender](https://github.com/Norbyte/bg3se)：没有它就没有这个项目
- [Move With Minimap](https://github.com/tldev/bg3-move-with-minimap-mod)：寻路验证落点的做法参考了它
- [VoteAnswers](https://github.com/HateTM/VoteAnswers)：读取对话选项的思路来自它

## 许可

[MIT](LICENSE) © 2026 Artimiss。Baldur's Gate 3 是 Larian Studios 的商标，本项目与其无关。

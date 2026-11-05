# 任务：哭声识别与语音记录的前端显示（Antigravity）

基线：`main` 的 `94f2908`。分支：`antigravity/cry-voice-ui`。完成后推送该分支，由 Claude 审查合并。

## 背景

后端已经上线两项功能（接口都已可用，见下）：

1. **哭声识别**：麦克风全天监听，识别到宝宝哭会记成一个“哭声时段”，**不保存任何录音**。
   这些时段已经出现在统一时间线和每日总结的要点里。不过前端还没有专门处理：时间线里的哭声条目现在被误标成「家长记录」。
2. **语音记录**：说「八戒，记一下宝宝喝了一百五十毫升奶」，会作为一条消息发给 AI 助手并保存，
   放在一个叫「语音记录」的对话里。现在页面上看不到结果。

本任务只改前端显示，让家长能看到这两项功能。

## 目标

1. **时间线里的哭声**（`static/timeline.js`、`static/timeline.css`）
   - 条目 `source === 'audio'` 且 `category === 'cry'` 时：来源标签显示「麦克风」，用醒目的暖红色，与「摄像头」「家长记录」「亲子互动」区分开。
   - 筛选区新增「哭声」开关，默认打开，和现有几个开关的写法保持一致。
   - 时间线顶部的统计格里新增一格「哭声」：显示 `summary.cry_count` 段，以及 `summary.cry_seconds` 换算成的分钟（不足 1 分钟显示「<1 分钟」）。
2. **今日总结卡片**（`static/timeline.js`）：后端已经在 `highlights` 里加了哭声那句，不用再改要点。
   如果 `stats.cry.count > 0`，在卡片里补一个小标注「哭声 N 段」，样式参考现有的小统计。
3. **监控页实时提示**（`static/app.js`、`static/index.html`、`static/app.css`）
   - `/api/status` 新增了 `cry: {active, since, last_score}`。`active` 为 true 时，在监控页顶部状态卡附近显示一条醒目提示：「检测到哭声 · HH:MM 起」，时间按北京时间显示，用 `app.js` 里已有的 `shanghaiParts` / `formatTime`。`active` 变回 false 后提示消失。
   - SSE（`/api/events`）会推送 `type === 'cry'` 的消息，字段为 `{active, since, score}` 或 `{active:false, until, peak}`。收到时调用现有的 `refreshStatus()` 即可，不用另写逻辑。
4. **语音记录提示**（`static/app.js`）：SSE 会推送 `type === 'voice_record'`，字段为 `{text, message, status, saved}`。
   收到后用现有的 `showToast()` 显示「语音记录：」加上 `message`。如果当前停留在 AI 助手页，再刷新一次会话列表（调用已有的 `window.Assistant.onPageShow()`）。
5. **设置页说明**（`static/index.html`、`static/app.js` 里的 `renderVoiceSettings`）
   - `/api/voice` 新增 `cry_detection: {enabled, ready, running}`。在「语音控制音乐」下面加一行只读的「哭声识别」：
     `running` 时显示「全天运行中 · 只记录哭声时间段，不保存录音」，`ready` 为 false 时显示「哭声模型未就绪」，`enabled` 为 false 时显示「已关闭」。不加开关。
   - 「语音唤醒词」这一行的说明改为：「每日 07:00–23:00 监听，唤醒后八秒内说出指令；可以控制音乐，也可以说“记一下……”来记录」。
6. 改了 `static/*.js`、`static/*.css` 的话，同时把 `static/index.html` 里对应的 `?v=` 版本号加 1。

## 允许修改的文件

- `static/timeline.js`、`static/timeline.css`
- `static/app.js`、`static/app.css`、`static/index.html`
- `tests/test_timeline_ui.cjs`、`tests/test_summary_ui.cjs`（补对应断言）

## 验收标准

- 时间线里哭声条目显示「麦克风」标签和独立颜色；可以用「哭声」开关筛掉；统计格显示段数和分钟数。
- 监控页在 `cry.active` 为 true 时显示提示，为 false 时不显示。
- 设置页能看到哭声识别的状态说明；唤醒词说明已更新。
- 手机宽度（375px）下这些新元素不换行错乱、不出现横向滚动。
- 命令全部通过：
  - `python -B -m unittest discover -s tests`
  - `node tests/test_timeline_ui.cjs`、`node tests/test_summary_ui.cjs`、`node tests/test_play_ui.cjs`、`node tests/test_assistant_ui.cjs`

## 禁止事项

- 不改 `baby_monitor/`、`server.py`、`model_server.py` 及任何后端文件、接口和数据库。
- 不碰 Jetson，不运行 `scripts/sync-jetson.sh`，不合并 `main`。
- 不新增 Demo/Real 范围切换。界面只有一套记录，接口固定用 `scope=real`。
- 时间一律按北京时间显示，不能用浏览器本地时区。
- 不用示例数据或 Mock 冒充真实结果。接口失败时如实显示失败或不显示。
- 不添加录音、音频播放或上传相关的功能。

# 任务：设置 / 陪我玩 / 收藏 / AI 助手 四页整理（Antigravity）

**开始条件：`antigravity/records-redesign` 已被 Claude 合并到 `main` 之后再开始。**
基线：届时 `main` 的最新提交。分支：`antigravity/ui-polish`。完成后推送该分支，由 Claude 审查合并。

## 背景

整体改版计划见 `docs/tasks/UI_REDESIGN_PLAN.md`。这四页功能都正常，问题在于排版平铺、开发术语多，
家长和评委看着乱。本任务只整理布局和文案，不改功能逻辑。

## 目标

1. **设置页**（`data-page="settings"`）：现在 11 行平铺。分成三组，每组一个小标题，组内保持现有卡片行样式：
   - 「功能」：监控访问、成人测试模式、语音控制音乐（这三个有开关）。
   - 「设备状态」：哭声识别、语音唤醒词、摄像头、本地视觉模型、照片存储（只读状态）。
   - 「账号与隐私」：数据隐私、安装到主屏幕、登录会话；以及底部的「辅助提醒说明」。
   - 文案改成家长能懂的话，例如：
     - 成人测试模式：「打开后观察画面里的成人，用于演示和调试；记录单独保存，不计入宝宝统计。」
     - 本地视觉模型的副标题不显示模型内部名（如 `EmotiEffLib + 时间状态机`），改为「本地运行，画面不上传」；
       右侧状态文字沿用现有逻辑。
   - 所有开关、状态的 id 和 JS 逻辑不变，只调整 HTML 分组顺序和文案。
2. **陪我玩页**（`data-page="play"`）：
   - 「亲子互动与反应记录」里的互动卡片默认只显示前 3 张，下面一个「查看全部 N 个互动」按钮，点了展开全部；
     切换上方分类按钮时同样只显示前 3 张。
   - 卡片里的「开始互动」从大黑按钮改成浅色描边小按钮（参考现有 `.timeline-filter` 一类的浅色风格）。
   - `app.js` 第 855–856 行附近的「Audio output …」改为中文：「音响已连接 · 队列还有 N 首」/「音响已连接」。
   - 「独立播放功能」说明卡改成播放器下方一行小字，不再单独占一张卡。
3. **收藏页**（`data-page="smiles"`，`#photoFilters`）：筛选按钮只默认显示「全部、笑脸、睡觉、清醒」，
   其余收进「更多」按钮，点开后显示全部；名称去掉「表情·」前缀（如「表情·平静」→「平静」）。筛选逻辑不变。
4. **AI 助手页**：
   - 顶部图标按钮（✎ 重命名、📋 生活记录、🗑 删除、+ 新建）：📋 与 + 显示文字「记录」「新对话」；
     ✎ 与 🗑 保留图标但确保有 `title` 和 `aria-label`。375px 宽度下顶部一行不换行、不溢出。
   - 输入框占位文字缩短为「记一下喂奶、睡眠，或问个问题…」。
5. 改了 `static/*.js`、`static/*.css` 的话，把 `static/index.html` 里对应的 `?v=` 版本号加 1。

## 允许修改的文件

- `static/index.html`（只改上述四页及 `?v=` 版本号）
- `static/app.js`（只改文案与收藏筛选的展开逻辑）、`static/app.css`
- `static/play.js`、`static/play.css`（互动卡片折叠与按钮样式）
- `static/assistant.js`、`static/assistant.css`（顶部按钮与占位文字）
- `tests/test_play_ui.cjs`、`tests/test_assistant_ui.cjs`（按新结构更新断言，并为互动卡片折叠补一条断言）

## 验收标准

- 设置页分三组，文案中没有 Prompt、Qwen、EmotiEffLib 等开发术语；开关功能照常。
- 陪我玩默认只显示 3 张互动卡片，可展开；没有英文开发文案。
- 收藏页默认 4 个筛选按钮 +「更多」。
- AI 助手顶部按钮含义清楚，375px 下不换行；输入框提示不被截断。
- 不出现整页横向滚动。
- 命令全部通过：
  - `python -B -m unittest discover -s tests`
  - `node tests/test_play_ui.cjs`、`node tests/test_assistant_ui.cjs`、`node tests/test_records.cjs`、
    `node tests/test_timeline_ui.cjs`、`node tests/test_summary_ui.cjs`

## 禁止事项

- 不改 `baby_monitor/`、`server.py`、`model_server.py` 及任何后端文件、接口和数据库。
- 不碰 Jetson，不运行 `scripts/sync-jetson.sh`，不合并 `main`。推送后 `git switch main` 切回 main。
- 不改「记录」「监控」两页和底部导航（另有任务在改）。
- 不用示例数据或 Mock 冒充真实结果；不复制受版权保护的文字、歌词或图片。

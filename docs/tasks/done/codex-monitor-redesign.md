# 任务：「监控」页改版——一张实时画面大卡 + 一排小状态（Codex）

基线：`main` 最新提交（任务书提交之后）。分支：`codex/monitor-redesign`。完成后推送该分支，由 Claude 审查合并。

## 背景

「监控」页（`data-page="monitor"`）现在从上到下：顶部大卡（`#heroState`，用快照做背景，叠「当前状态」）、
实时画面卡（`.camera-card`，又显示同一帧）、两个小卡（睡眠姿势、本地 AI）、睡醒提醒按钮、手动校正折叠块。
问题：

1. 同一帧画面出现两次，占了将近两屏。
2. 「本地 AI」小卡写着「EmotiEffLib + 时间状态机」，是开发术语，家长看不懂。
3. 哭声识别只有在哭的时候才出现横幅（`#cryAlertBanner`），平时看不到它在工作。

## 目标

1. **合并成一张实时画面大卡**（替换 `#heroState` 与 `.camera-card`）：
   - 卡片主体显示实时画面（沿用 `#cameraStream` 的现有加载/刷新逻辑），比例 16:9，圆角与现有卡片一致。
   - 画面左上角：`LIVE` 小标（沿用 `#cameraLiveBadge` 逻辑）。
   - 画面底部渐变遮罩上叠：状态小标（`#activityChip`）、大字状态（`#activityTitle`）、持续时间（`#activityDuration`）。
   - 摄像头不可用时，沿用 `#cameraOffline` 的提示，显示在这张卡里。
   - 删除只为旧顶部大卡服务的快照背景代码（`refreshHeroSnapshot`、`#heroPhoto` 相关），不能影响 `#cameraStream`。
   - 分辨率文字（`#cameraResolution`）不再显示在主界面。
2. **下方一排三个小状态**（替换现有 `.status-grid` 两张卡），等宽三列：
   - 「睡姿」：沿用 `#postureTitle` / `#postureDuration` 的数据。
   - 「哭声」：取 `/api/status` 的 `cry`：`active` 为 true 显示「正在哭」（暖红色），为 false 显示「安静」；
     若接口没有 `cry` 字段或哭声识别未就绪，显示「未开启」。`#cryAlertBanner` 横幅保持现有逻辑不变。
   - 「AI 识别」：沿用 `#modelTitle` 的现有状态逻辑（运行中/已关闭/低光暂停/出错等），副文字改成家长能懂的话，
     例如「本地运行，不上传」；删除「EmotiEffLib + 时间状态机」字样。
3. 睡醒提醒按钮、「手动校正 / 调试」折叠块保留，放在三个小状态下方，逻辑不变。
4. 状态必须如实：画面过期、低光暂停、离线、模型失败等现有分支的文字和样式都要保留在新布局里，不能显示成正常。
5. 改了 `static/*.js`、`static/*.css` 的话，把 `static/index.html` 里对应的 `?v=` 版本号加 1。

## 允许修改的文件

- `static/index.html`（只改 `data-page="monitor"` 这一段和 `?v=` 版本号）
- `static/app.js`（只改监控页相关函数：`renderStatus`、快照刷新、摄像头加载等）
- `static/app.css`（监控页相关样式）
- 新增 `tests/test_monitor_ui.cjs`：仿照 `tests/test_records.cjs` 的写法，覆盖：正常状态、画面过期、
  低光暂停、哭声 active/false/缺字段三种显示

## 验收标准

- 监控页只有一张画面大卡，同一帧不再出现两次；首屏（375×812）能看到画面、当前状态和三个小状态。
- 三个小状态按上面规则显示；哭声小状态随 `/api/status` 刷新。
- 各种异常状态（过期/低光/离线/模型失败）在新布局里文字如实。
- 不出现整页横向滚动。
- 命令全部通过：
  - `python -B -m unittest discover -s tests`
  - `node tests/test_monitor_ui.cjs`、`node tests/test_records.cjs`、`node tests/test_timeline_ui.cjs`、
    `node tests/test_play_ui.cjs`、`node tests/test_assistant_ui.cjs`

## 禁止事项

- 不改 `baby_monitor/`、`server.py`、`model_server.py` 及任何后端文件、接口和数据库。
- 不碰 Jetson，不运行 `scripts/sync-jetson.sh`，不合并 `main`。推送后 `git switch main` 切回 main。
- 不改「记录」页（Antigravity 正在 `antigravity/records-redesign` 上改）、底部导航和其他页面。
- 不用示例数据或 Mock 冒充真实结果。

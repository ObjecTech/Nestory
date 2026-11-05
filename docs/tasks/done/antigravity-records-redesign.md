# 任务：「记录」页改版——去重、加全天色带、收起大模型观察（Antigravity）

基线：`main` 最新提交（含新底部导航）。分支：`antigravity/records-redesign`。完成后推送该分支，由 Claude 审查合并。

## 背景

「记录」页（`data-page="records"`）现在从上到下有 7 块：标题 + 模式下拉框 + 日期、今日总结、
统一时间线、睡眠/清醒两个小方块、状态时间段、睡姿时间段、大模型观察。用户觉得很乱，问题是：

1. **同样的数据出现两三遍**：统一时间线（`/api/timeline/unified`）已经包含摄像头的状态、睡姿、
   在不在画面，下面「状态时间段」「睡姿时间段」又各列一遍；睡眠/清醒总时长在时间线统计格里也有。
2. **噪音多**：一天 35 条时间线里约 15 条「不在画面」、13 条睡姿，真正重要的事件被淹没。
3. **模式下拉框多余**：2026-10-18 已定规则——视觉记录跟随设置页「成人测试模式」开关，不让用户在这里再选。
4. **大模型观察平铺**：每条 40 多字描述，一天十几条，全部展开在页面底部。

本任务只改前端。所需数据现有接口都已提供，不改后端。

## 目标（改版后从上到下 4 块）

1. **页头**（`static/index.html`）：只保留「状态记录」标题和日期选择。删除 `#recordMode` 下拉框。
   - `app.js` 里请求 `/api/timeline` 与 `/api/observations` 的 `mode` 参数，改为取 `/api/status`
     返回的 `observation_mode`（现有 `syncRecordMode` 已在拿这个值，改成存到 `state` 里即可，例如
     `state.observationMode`，默认 `infant`）。模式变化时自动重新加载记录。
   - 删除 `state.recordModeTouched` 及相关逻辑。
2. **今日总结卡**（`#dailySummaryCard`，`static/timeline.js` 的 `renderSummary`）：保持现有内容。
   删除页面上独立的 `.summary-grid`（`#sleepTotal`、`#awakeTotal`）——这两个数已在时间线统计格里。
3. **今日时间线**（`#unifiedTimelineCard`，`renderTimeline`），分两层：
   - **全天色带**（新）：时间线卡片顶部一条 0:00–24:00 的横向色带，高约 18px，圆角。
     用 `items` 里 `source === 'vision'` 且 `category === 'activity'` 的条目按 `start_at`/`end_at`
     铺色：睡眠、清醒、未知各一种颜色（复用 `app.js` 里 `activityColors` 的色调）；再用
     `category === 'presence'` 且 `type === 'not_visible'` 的条目覆盖为浅灰斜纹，表示「不在画面」。
     下方标 0、6、12、18、24 五个刻度。当天未结束的部分留白。色带下放一行小图例。
   - **点色带的某一段**：在色带下方展开一个小面板，显示这段的状态、起止时间、时长，以及这段时间内的
     大模型观察（`/api/observations` 结果中与该段时间重叠的条目：观察 `ts`～`end_ts` 与段落有交集），
     每条显示时间 + 描述。再点一次或点别的段收起/切换。没有观察时显示「这段时间没有模型描述」。
   - **事件列表**：列表默认**不再列出**摄像头的 `posture` 与 `presence` 条目（它们已体现在色带里）。
     摄像头 `activity` 条目只列出「睡眠」段（入睡～醒来），清醒/未知段不列。其余来源（哭声、家长记录、
     互动、抓拍）照常列出。
   - **筛选**：把现有 5 个复选框换成一行可横向滑动的圆角按钮（单选）：全部 / 睡眠 / 喂养 / 哭声 / 互动。
     「喂养」= 家长记录里的喂奶等照护条目（现有 `care` 判断）。「全部」= 上面规则过滤后的全部事件。
     不再提供显示睡姿/在不在画面的开关。
   - 时间线顶部现有的统计格保留。
4. **AI 观察日志**（原「大模型观察」卡片）：移到页面最底部，改成默认**收起**的折叠块，标题行显示
   「AI 观察日志 · N 条」和展开箭头，点击展开后显示现有的观察卡片列表（`renderObservations` 的内容不变）。
   删除「状态时间段」「睡姿时间段」两张卡片（`#activityTimeline`、`#postureTimeline`、`#recordCount`）
   以及 `app.js` 里只为它们服务的渲染代码。
5. 改了 `static/*.js`、`static/*.css` 的话，把 `static/index.html` 里对应的 `?v=` 版本号加 1。

## 允许修改的文件

- `static/index.html`、`static/app.js`、`static/app.css`
- `static/timeline.js`、`static/timeline.css`
- `tests/test_records.cjs`、`tests/test_timeline_ui.cjs`、`tests/test_summary_ui.cjs`（按新结构更新断言，
  并为色带铺色、点段显示观察、默认不列睡姿/在不在画面、筛选按钮各补至少一条断言）

## 验收标准

- 「记录」页从上到下只有：页头（标题 + 日期）、今日总结、今日时间线（色带 + 筛选 + 事件列表）、收起的 AI 观察日志。
- 页面上没有模式下拉框；设置页切换成人测试模式后，回到记录页数据随之切换。
- 色带按时间正确铺色；点某一段能看到该时段的模型描述。
- 默认事件列表里没有「不在画面」和睡姿条目；5 个筛选按钮工作正常。
- AI 观察日志默认收起，展开后内容与之前一致。
- 手机宽度（375px）下不换行错乱、不出现整页横向滚动（筛选按钮行自身可以横滑）。
- 时间一律按北京时间（Asia/Shanghai），用已有的 `shanghaiParts` / `formatTime`，色带位置也按北京时间算。
- 命令全部通过：
  - `python -B -m unittest discover -s tests`
  - `node tests/test_records.cjs`、`node tests/test_timeline_ui.cjs`、`node tests/test_summary_ui.cjs`、
    `node tests/test_play_ui.cjs`、`node tests/test_assistant_ui.cjs`

## 禁止事项

- 不改 `baby_monitor/`、`server.py`、`model_server.py` 及任何后端文件、接口和数据库。
- 不碰 Jetson，不运行 `scripts/sync-jetson.sh`，不合并 `main`。推送后 `git switch main` 切回 main。
- 不新增 Demo/Real 范围切换。界面只有一套记录，统一时间线接口固定用 `scope=real`。
- 不用示例数据或 Mock 冒充真实结果。接口失败时如实显示失败。
- 不引入新的第三方库或外部 CDN；色带用纯 HTML/CSS（或内联 SVG）实现。

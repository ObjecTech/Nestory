# 任务：今日总结与 7 天趋势（Gemini）

基线分支：`claude/assistant-demo`（在统一时间线任务合并后再开始）。在新分支 `gemini/daily-summary-ui` 上工作，完成后推送该分支，由 Claude 审查合并。

## 目标

在「状态记录」页的统一时间线卡片**上方**，新增“今日总结”卡片。Demo / Real 和日期与时间线卡片共用同一套控件。

1. **要点**：逐条显示 `highlights`（后端已生成完整句子，前端不要自己拼数字）。
2. **AI 润色**：放一个“AI 润色”按钮，点击后请求 `narrative=1`，请求期间显示“AI 正在整理…”。
   - `narrative_status=ok`：在要点上方用引用样式显示 `narrative`。
   - 其他状态：显示“AI 暂时无法润色，以上为记录汇总”。**不要用示例文字代替模型结果。**
3. **建议**：`suggestions` 非空时显示为“小提醒”列表。
4. **7 天趋势**：用纯 CSS 柱状图（不引入图表库）显示 `trend` 中每天的喂奶次数和家长记录的睡眠时长（小时），可在两项之间切换；`null` 视为 0。点击某一天时，把页面日期切换到那一天。
5. **免责声明**：卡片底部始终显示 `disclaimer`。

## 接口（Claude 已实现）

鉴权与其他接口相同（Session Cookie）；只支持 GET，且一次只查一天。

```
GET /api/summary/daily?scope=demo&date=2026-10-15&timezone=Asia%2FShanghai[&narrative=1]
→ 200 {
  "api_version":"1","scope":"demo","timezone":"Asia/Shanghai","date":"2026-10-15",
  "stats":{"feeding":{"count":2,"total_ml":210,"last_at":"13:00","average_gap_minutes":240},
           "sleep":{"parent_seconds":3600,"sessions":2,"longest_seconds":3600},
           "diaper":{"count":1,"dirty":1},
           "interaction":{"count":1,"minutes":5,"reactions":{"开心":1},"favorite":"小手拍拍"},
           "visual":{"sleeping":1800,"available":true,"smiles":2,"reliability":"unverified"}},
  "highlights":["记录了 2 次喂奶，共 210 ml，最近一次在 13:00，平均间隔约 4 小时。", "..."],
  "suggestions":["可以去「陪我玩」挑一个亲子互动，并记录宝宝的反应。"],
  "trend":[{"date":"2026-10-09","feeding_count":0,"feeding_ml":null,"parent_sleep_seconds":null,
            "diaper_count":0,"interaction_count":0}, "…共 7 天，最后一天是 date"],
  "narrative":null,"narrative_status":"not_requested",
  "disclaimer":"以上为记录汇总，仅供参考，不构成医疗建议。","truncated":false
}
```

- `narrative_status` 的取值：`not_requested`、`ok`、`rejected`（模型写出了事实中没有的数字，已被丢弃），以及模型错误码（如 `model_timeout`、`queue_full`）。
- 带 `narrative=1` 的请求可能要等几秒到几十秒（需要排队等摄像头分析结束），前端超时时间至少设为 60 秒。
- 摄像头相关数字只能放在单独的“摄像头观察（仅供参考）”区域，不要和家长记录相加。

## 允许修改的文件

- `static/index.html`（只改 records 区块）
- 新建 `static/summary.js`、`static/summary.css`，或在时间线任务的 `static/timeline.js` / `static/timeline.css` 中扩展
- 新建 `tests/test_summary_ui.cjs`：用 fetch 桩覆盖要点渲染、润色成功、`rejected` / 失败提示、趋势柱状图和点击切换日期
- 如果新增了静态文件，按需更新 `static/service-worker.js` 的缓存列表

## 验收标准

- 375px 手机宽度可用，无横向滚动；柱状图在手机上能看清 7 根柱子。
- `node tests/test_summary_ui.cjs`、现有 cjs 测试和 `python -B -m unittest discover -s tests` 全部通过。
- 接口失败时显示错误，不展示示例数据。

## 不要碰

- 后端、数据库、`/api/timeline`（旧接口）及现有 records 逻辑。
- 不引入外部库或 CDN；不合并 main，不 force push。

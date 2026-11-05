# 任务：统一时间线页面（Gemini）

基线分支：`claude/assistant-demo`（G2 合并后请先同步）。在新分支 `gemini/unified-timeline-ui` 上工作，完成后推送该分支，由 Claude 审查合并。

## 目标

在「状态记录」页（`data-page="records"`）顶部新增“今日时间线”卡片：把摄像头识别、家长记录、亲子互动放在同一条时间轴上展示。这是课程演示中“三模块闭环”的主要画面。页面上现有的状态时间段、睡姿和大模型观察卡片保持不动，放在新卡片下方。

1. **控件**：Demo / Real 切换（默认 Demo，持续显示 Demo 徽标）。日期直接复用页面上已有的 `#recordDate`。
2. **汇总卡**：喂奶次数和总毫升数、家长记录的睡眠时长、换尿布次数、互动次数。另有一张单独的“摄像头观察（仅供参考）”卡，显示 `visual_activity_seconds` 里睡着 / 清醒 / 哭闹的时长。值为 `null` 时显示“—”。
3. **时间轴列表**：按 `start_at` 升序显示。每行包括本地时间（有 `end_at` 时显示时间段）、`label`、来源标签，按来源配色：
   - `vision` → “摄像头”（`category` 为 `moment` 的是时间点事件，例如“笑了”）
   - `parent_chat` / `parent_manual` → “家长记录”
   - `interaction` → “互动”，并显示反应（开心 / 平静 / 没兴趣 / 哭闹）
4. **筛选**：四个开关：摄像头状态（`category=activity`）、睡姿与在画面（`posture`、`presence`）、家长记录、互动。默认关闭睡姿与在画面，避免列表过长。
5. **提示**：始终显示 `warnings[0]`。`visual.available=false` 时显示“暂无摄像头数据”。`truncated=true` 时提示“记录较多，仅显示部分”。

## 接口（Claude 已实现）

鉴权与其他接口相同（Session Cookie）；只支持 GET。

```
GET /api/timeline/unified?scope=demo&date=2026-10-15&timezone=Asia%2FShanghai
→ 200 {
  "api_version":"1","scope":"demo","timezone":"Asia/Shanghai","from":"2026-10-15","to":"2026-10-15",
  "visual":{"observation_mode":"adult_test","continuity_verified":false,"available":true},
  "items":[{"id":"care:…","source":"parent_chat","category":"care","type":"feeding","label":"喂奶 150 ml",
            "start_at":"…Z","end_at":null,"duration_seconds":null,"reliability":"parent_reported",
            "amount_ml":150,"diaper_type":null,"notes":""},
           {"id":"vision:interval:12","source":"vision","category":"activity","type":"sleeping","label":"睡着",
            "start_at":"…Z","end_at":"…Z","duration_seconds":1800,"open":false,"reliability":"unverified",
            "confidence":0.8,"observation_mode":"adult_test"},
           {"id":"interaction:…","source":"interaction","category":"interaction","type":"finger_play",
            "label":"小手拍拍","reaction":"happy", …}],
  "summary":{"feeding_count":2,"feeding_ml":240,"diaper_count":1,"parent_sleep_seconds":3000,
             "interaction_count":1,"visual_activity_seconds":{"sleeping":1800},"visual_reliability":"unverified"},
  "truncated":false,
  "warnings":["摄像头监测连续性未验证，视觉时长仅供参考，不能与家长记录相加。"]
}
```

- Demo 范围的摄像头数据来自“成人测试”画面，Real 范围只显示婴幼儿模式；这些都由后端决定，前端不要自己过滤观察模式。
- `open=true` 的区间表示仍在进行，结束时间显示“进行中”。
- 不要把摄像头时长和家长睡眠时长相加，也不要显示合计数字。

## 允许修改的文件

- `static/index.html`（只改 records 区块，新增卡片）
- 新建 `static/timeline.js`、`static/timeline.css`，并在 index.html 中引入
- 新建 `tests/test_timeline_ui.cjs`：用 fetch 桩验证渲染、筛选、`available=false` 与 `truncated` 提示，以及接口出错时显示错误信息
- 如果新增了静态文件，按需更新 `static/service-worker.js` 的缓存列表

## 验收标准

- 375px 手机宽度可用，无横向滚动；列表超过 50 条时卡片内部滚动。
- `node tests/test_timeline_ui.cjs`、现有 cjs 测试和 `python -B -m unittest discover -s tests` 全部通过。
- 接口失败时显示错误，不展示示例数据。

## 不要碰

- 后端、数据库、`/api/timeline`（旧接口）及现有 records 逻辑。
- 不引入外部库或 CDN；不合并 main，不 force push。

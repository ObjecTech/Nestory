# 任务：「陪我玩」亲子互动卡片与反应记录 UI（Gemini）

基线分支：`claude/assistant-demo`。在新分支 `gemini/play-interactions-ui` 上工作，完成后推送该分支，由 Claude 审查合并。

## 背景

「陪我玩」页面（`data-page="play"`）已有音乐类目和播放器。本任务在同一页面、播放器下方增加“亲子互动”区块。后端接口已实现（见下）。保存失败时 UI 必须如实显示“保存失败”，**不能假装成功**。

## 目标

1. **互动内容目录**：新建 `static/play/activities.json`，放 8–10 个原创互动，覆盖三类 `finger_play`（手指操）、`game`（亲子游戏）、`sensory`（感官探索）。每项包含：
   `{"id":"fp-01","kind":"finger_play","title":"...","age_months":[6,24],"minutes":3,"steps":["...","..."]}`
   文字必须原创，**不得抄写儿歌歌词或受版权保护的内容**，每项 2–4 个简短步骤。
2. **互动卡片列表**：按类别分组显示卡片（标题、适龄、时长）。点开后显示步骤和“开始”按钮。
3. **计时与反应记录**：点“开始”后显示进行中计时；点“结束”后弹出孩子反应选择（开心 / 平静 / 没兴趣 / 哭闹）和可选备注（不超过 200 字），然后调用保存接口。
4. **今日记录**：区块底部列出今天已保存的互动（时间、标题、反应）。
5. **范围**：在区块标题旁放 Demo / Real 切换，默认 Demo，并保存在 `localStorage`（读写放在 try/catch 中）。Demo 状态持续显示徽标。

## 接口（Claude 已实现，`claude/assistant-demo` 上可用）

鉴权与现有接口相同：Session Cookie，非 GET 请求带 `Origin` 和 `X-CSRF-Token`（复用 `static/app.js` 的 `api()` 写法）。错误统一为 `{"api_version":"1","error":{"code","message","retryable",...},"request_id"}`。

```
POST /api/interactions
{"client_id":"<uuid 或 [A-Za-z0-9_.-]{1,128}>","scope":"demo","activity_id":"fp-01","kind":"finger_play",
 "title":"...","timezone":"Asia/Shanghai","started_at":"2026-10-15T07:00:00Z","ended_at":"2026-10-15T07:03:00Z",
 "reaction":"happy","notes":""}
→ 201 {"api_version":"1","replayed":false,"interaction":{"id","scope","client_id","activity_id","kind","title",
        "timezone","started_at","ended_at","duration_seconds","reaction","notes","created_at"}}
   同一 client_id 重发相同内容 → 200（replayed=true，同一条）；内容不同 → 409 idempotency_conflict
   校验失败 → 422 validation_error

GET /api/interactions?scope=demo&date=2026-10-15&timezone=Asia%2FShanghai
→ 200 {"api_version":"1","scope","timezone","from","to","items":[...按 started_at 倒序，最多 200 条]}

DELETE /api/interactions/{id}?scope=demo   → 200 {"deleted":true}；不存在 → 404（UI 可选用）
```

- 必填：`client_id`、`scope`、`activity_id`（≤64 字）、`kind`、`title`（≤100 字）、`started_at`。
- `kind`：`finger_play` | `game` | `sensory` | `music`（`music` 留给播放器以后用）。
- `reaction`：`happy` | `calm` | `uninterested` | `fussy` | `null`；`notes` ≤200 字。
- `ended_at` 可为 null；不得早于 `started_at`，单次不超过 6 小时，不能是未来时间。
- 时间一律用 UTC ISO 8601，显示时转换为本地时间。同一次保存重试时必须复用同一个 `client_id`。

## 允许修改的文件

- `static/index.html`（只改 play 区块）
- `static/app.js` 或新建 `static/play.js`（推荐新建，并在 index.html 中引入）
- `static/app.css` 或新建 `static/play.css`
- 新建 `static/play/activities.json`
- 新建 `tests/test_play_ui.cjs`（参照 `tests/test_assistant_ui.cjs` 的写法，用 fetch 桩验证：成功 201、失败 404/422 时显示错误）

如新增静态文件，需检查 `static/service-worker.js` 的缓存列表是否需要同步更新。

## 验收标准

- 现有音乐播放功能和样式不受影响。
- 保存失败时卡片显示错误并保留已填写的反应，可以重试；成功后出现在“今日记录”中。
- 375px 手机宽度可用，无横向滚动。
- `node tests/test_play_ui.cjs` 以及现有 cjs 测试、`python -B -m unittest discover -s tests` 全部通过。

## 不要碰

- 后端（`server.py`、`baby_monitor/`、`model_server.py`）、数据库、调度、部署脚本。
- 音乐播放器的现有接口与逻辑。
- 不引入外部 CDN 或第三方库；不提交任何音频或图片版权素材。
- 不合并 main，不 force push。

# 任务：Assistant 演示体验小改（Gemini）

基线分支：`claude/assistant-demo`。在新分支 `gemini/assistant-demo-polish` 上工作，完成后推送该分支，由 Claude 审查合并。

## 目标

让家长在真实 2B 较慢或失败时，也能看懂当前状态，并知道下一步怎么做。

1. **失败提示改为中文友好文案**。按 `msg.error.code` 映射，未知 code 时保留后端给的 `message`：
   - `model_timeout` / `invalid_model_output` / `model_failed`：“这次没能理解，请换个说法或点下方示例。”
   - `queue_full`：“AI 正忙，请稍后重试。”
   - `model_unavailable` / `assistant_disabled` / `service_restarted`：“本地 AI 暂不可用，请稍后再试。”
   - 现有“重试”按钮逻辑（`retryable`）保持不变。
2. **等待提示**：状态为 queued/running 超过 5 秒时，在状态文字后面追加“（摄像头分析占用中，稍候）”；只是文案变化，不修改轮询间隔。
3. **快捷示例**：在现有 4 个 chip 的基础上补 2 个，文案必须是完整的明确句子：
   - “今天上午9点宝宝喝了120ml奶”
   - “今天宝宝喝了多少奶？”

## 允许修改的文件

- `static/assistant.js`（只改渲染/文案相关函数）
- `static/assistant.css`
- `tests/test_assistant_ui.cjs`（按需补断言）

## 验收标准

- `node tests/test_assistant_ui.cjs`、`node tests/test_assistant_network.cjs` 通过。
- `python -B -m unittest discover -s tests` 通过（其中 `test_assistant_m1` 会自动启动隔离服务并运行 `tests/test_assistant_m1.cjs`，不要单独用 node 运行它）。
- 手机宽度（375px）下 chip 自动换行，不出现横向滚动。
- 提交说明写清改动，附一张手机宽度截图（截图不要提交进 Git）。

## 不要碰

- 任何 API 路径、请求字段、轮询间隔、scope 逻辑。
- Mock 模式：保持“仅回环且 `?assistant_mock=1` 时启用并显示横幅”，不要扩展。
- 后端 Python 文件、`model_server.py`、数据库、`docs/ASSISTANT_API_CONTRACT.md`。
- 不要合并 main，不要 force push。

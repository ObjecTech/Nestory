# Nestory 界面改版计划（2026-10-20）

用手机宽度（375px）逐页检查后的结论与分工。每一项都有单独的任务书，按顺序做，避免多个分支同时改同一块。

## 各页问题与改法

| 页面 | 问题 | 改法 | 谁做 | 任务书 |
| --- | --- | --- | --- | --- |
| 全局 · 底部导航 | 6 个图标是同一张占位心电图，下面还有英文小字 | 换成 6 个线条图标，选中项浅绿底 | Claude（已完成） | — |
| 记录 | 同一批视觉数据出现 2～3 遍；模式下拉框多余；观察描述平铺 | 全天色带 + 重要事件 + 收起的观察日志 | Antigravity | `antigravity-records-redesign.md` |
| 监控 | 同一帧画面出现两次（顶部大卡背景 + 实时画面卡）；「本地 AI · EmotiEffLib + 时间状态机」是开发术语；哭声状态只在出事时出现 | 合成一张实时画面大卡，状态叠在画面上；下方一排小状态（睡姿 / 哭声 / AI） | Codex（涉及状态逻辑，不能把失败状态显示成正常） | `codex-monitor-redesign.md` |
| 设置 | 11 行平铺，开关和状态混在一起；「Prompt」「Qwen」等术语 | 分成「功能」「设备状态」「账号与隐私」三组，说明改成家长能懂的话 | Antigravity | `antigravity-ui-polish.md` |
| 陪我玩 | 8 张互动卡片每张一个大黑按钮，页面很长；「Audio output 已连接到授权网络」等英文开发文案 | 互动卡片默认显示 3 张 +「查看全部」；按钮减轻；文案中文化 | Antigravity | `antigravity-ui-polish.md` |
| 收藏 | 14 个筛选按钮，「表情·平静」这类名称生硬 | 常用 4 个 +「更多」展开 | Antigravity | `antigravity-ui-polish.md` |
| AI 助手 | 顶部 ≡ ✎ 📋 🗑 + 几个图标看不懂；输入框提示被截断 | 图标配文字或长按提示；缩短输入提示 | Antigravity | `antigravity-ui-polish.md` |

## 顺序

1. 现在并行：Antigravity 做「记录」，Codex 做「监控」（两者改的是 `index.html`、`app.js`、`app.css` 的不同区域，Claude 合并时处理冲突）。
2. 「记录」合并后：Antigravity 做 `antigravity-ui-polish.md`。
3. 每个分支合并后由 Claude 上线 Jetson 并在手机宽度下复查。

# Nestory Assistant M1 集成与功能闭环验收

日期：2026-10-13。结论：真实前端、真实 HTTP 后端与隔离 SQLite 已形成可重复验证的业务闭环。模型使用合成实现；本报告不代表真实 Qwen/GPU 或 Jetson 硬件验收。

## 交付与来源

- 集成分支：`codex/assistant-m1-integration`；本报告与修复代码一起提交，交付 commit 以该分支本次最终提交为准。
- 集成基底：内部 main `82c5015`，保留其中的 ADR v2.1 文档与其他历史。
- 后端：合并 `7370e1c`；前端：合并 Gemini 交付 `88d7d74`。内部共享仓库中的 `assistant-frontend` 当前另有 main 文档合并，未使用这些合并授权新功能。
- 冻结契约：`7cf02ac` 的 `docs/ASSISTANT_API_CONTRACT.md` 原样保留；13 个方法/路径、字段和状态语义未修改。
- 使用独立集成 worktree，没有切换 Gemini 的目录。指定前端 commit 已在共享 Git 历史中可读；先建立该 commit 的独立只读副本，用后端 launcher 的 `--static-dir` 联调，然后才将前端合并到集成分支。
- 仅同步本功能分支至内部共享裸仓库；未合并 main、未公开发布、未更新 Jetson 工作副本或重启服务。

## 真实 HTTP 联调结果

全部通过。HTTP 202 只代表接受；完成与失败均由 GET 轮询的 `status` 判断，失败轮询 HTTP 200 不会生成虚假助手回复或保存提示。

| 方法与路径（前缀 `/api/assistant`） | 验收结果 |
|---|---|
| GET `/conversations` | Demo/Real 列表与游标、历史恢复通过 |
| POST `/conversations` | HTTP 201，独立 A/B 对话通过 |
| PATCH `/conversations/{id}` | 重命名通过 |
| DELETE `/conversations/{id}` | 删除消息、保留生活记录、推理中删除通过 |
| GET `/conversations/{id}/messages` | 历史、pending_turn、分页与卡片恢复通过 |
| POST `/conversations/{id}/messages` | HTTP 202、幂等 replay、响应丢失后重发通过 |
| GET `/conversations/{id}/messages/{user_message_id}` | queued/running/completed/failed、错误与事实通过 |
| POST `/conversations/{id}/messages/{user_message_id}/record-actions/{action_id}/confirm` | 真实确认写入、重复确认通过 |
| GET `/care-events` | 日期/范围隔离与分页通过 |
| POST `/care-events` | 独立手动记录、重复请求不重复写入通过 |
| PATCH `/care-events/{id}` | expected_version、版本递增、409 冲突通过 |
| DELETE `/care-events/{id}` | expected_version、软删除、统计排除通过 |
| GET `/sleep-summary` | 真实接口、范围与来源可靠性通过；既有睡眠并集测试仍通过 |

`tests/test_assistant_m1.py` 启动实际 BabyMonitorApp、MonitorServer、AssistantService 和临时双库；Node 加载交付的 `static/assistant.js` 控制器，通过真实 `/login`、Session Cookie、Origin、CSRF 与 HTTP API 执行业务。DOM 辅助器用于控制器断言，不冒充浏览器验收。另有独立真实浏览器验证。

测试没有放宽 Host、网络入口、登录、Session、Origin 或 CSRF。开发服务仅监听回环，未打开物理摄像头/麦克风，未读取生产 SQLite，未调用 Qwen。自动化测试对时钟使用隔离的过去日期下午，保证“今天上午 11 点”在每次运行时都是已完成的模拟事件；这只存在于测试 fixture，不修改产品时钟或数据库时间策略。

## 业务与数据库证据

- A/B 创建、切换、重命名、删除、历史加载、刷新恢复有效选择均通过；后台回复不进入其他对话，延迟 Demo 请求不覆盖 Real 页面。
- A 输入“今天上午 11 点宝宝喝了 150ml 奶”，得到 saved 卡片和真实 `care_events` 行；B 查询当天喂养得到 1 次、150 ml。
- 更正事件至 155 ml、version 2；旧 version 修改得到 409。
- 删除 A 后，A 的消息和对话查询为 404，事件仍存在，`source_message_id` 为 NULL；独立删除事件后为 deleted、version 3，B 再查询为 0 次。
- Python 直接核对 SQLite：该事件保留三条 create/update/delete 审计修订；A 消息消失；已确认事件及独立 Real 手动记录仍保留。
- 模糊喂奶先返回 needs_confirmation，数据库没有事件；点击实际控制器渲染的确认按钮、补充信息后才产生正式事件。重复确认没有第二条事件。
- 相同 client_message_id/client_event_id 重发无重复；故意丢弃已接受 POST 的网络响应后再发送，复用原 ID，SQLite 只有一条 80 ml 记录。
- 模拟推理失败和超时：HTTP 轮询为 failed，保留错误，无助手成功回复、无记录卡片。新增真实回环 HTTP socket 超时测试，确认实际传输超时同样失败且零事件写入。
- 推理中删除对话不会被工作线程重建；未登录 401、缺 CSRF 403、错误 Origin 403、跨 scope ID 404 均通过。
- 实际停止并重建测试 app/service，清除登录 session、重新登录后，消息、会话和事件从同一测试数据库恢复。此前 queued/running 的启动恢复逻辑及事务回滚沿用后端回归测试。
- 额外验证超过 50 个会话、50 条消息、50 个事件的分页；有效已选对话即使落在列表首屏之外，也能恢复。

## 已修复的问题

1. 删除 404/501 和网络失败自动切换前端内存 Mock 的逻辑。401 进入登录；404 明确显示不存在；503 与失败轮询显示真实错误；断网保留草稿并显示连接失败。
2. Mock 仅可在 CommonJS 测试中显式 `setMock(true)`，或在回环开发页显式使用 `?assistant_mock=1`。普通页面不能开启；横幅明确“仅在内存、未写入数据库”。M1 业务与浏览器测试均为 `isMock=false`。
3. 记录卡片确认改用用户消息 ID，并补充发生/结束时间输入；未说明的饮用量不预填猜测值。
4. 历史加载经现有轮询接口恢复 record_actions、facts 和 failed 错误；仅可重试错误显示重试按钮；喂养事实不再误显示睡眠卡。
5. 编辑表单不 PATCH 不可变 event_type；正确附带 expected_version。已删除事件的历史卡片显示“已删除”。
6. 中文时间允许“上午 11 点”等自然空格；只扩展已支持的表达，不放宽过去事件、数量和事务校验。
7. 增加选择/列表请求序号和 scope 校验，切换时清空旧内容，取消跨范围轮询；迟到结果不污染当前页。
8. 发送响应丢失时保留 client_message_id；会话删除清理相应待发请求。成功发送不抹掉用户切换后新输入的草稿。
9. 生活记录加载错误不再显示成“空记录”；会话/消息/事件接上已有游标接口；开发合成模型补齐原有 feeding_summary/care_list 查询 fixture。
10. 手机标题改为两行布局，避免只显示一个字；关闭的抽屉/弹窗不再留在可见布局中；JS/CSS 版本更新避免旧缓存。

## 测试结果与复现

最终回归命令：

```bash
python3 -B -m unittest discover -s tests -v
node tests/test_assistant_ui.cjs
node --test tests/test_records.cjs tests/test_assistant_network.cjs
git diff --check
```

结果：**161 项 Python 测试，160 通过、1 项既有可选拼音测试跳过；原前端脚本 15 项逻辑场景通过；Node runner 7 项通过（记录页 1、网络/Mock 回归 6）**。真实 JS/HTTP/SQLite 集成和应用重启验收由 Python 测试入口自动运行，不依赖浏览器、模型权重或设备。

使用隔离 launcher 复现真实浏览器验证：

```bash
python3 -B scripts/run-assistant-dev.py --port 18881
```

选择至少 14 位的开发密码；在 `http://127.0.0.1:18881/` 用 `assistant-dev` 登录。使用 Playwright CLI 独立浏览器会话，在登录后执行 `tests/assistant_browser_m1.js` 中的 async function。完成后检查 `window.__M1BrowserResult.result === 'PASSED'`；原生删除确认由测试 handler 接受，CLI 可能先返回 modal state，须等最终结果，不把这次提前返回当成功。凌晨运行时 fixture 使用昨天的已完成事件。

实际浏览器验收：**1280×900 桌面、390×844 手机宽度均通过**。两种宽度均执行导航、新建、重命名、发送、待确认补填、编辑、历史抽屉、刷新、跨会话查询、删除对话、单独删除事件、断网反馈。观察到真实 200/201/202；`isMock=false`；无横向溢出。手机宽度是桌面 Chromium 的视口测试，并非真实手机/软键盘或 Safari 验收。

截图保存在忽略的 `output/playwright/m1-desktop.png`、`m1-phone.png`；已人工查看布局。浏览器断网测试出现预期 ERR_INTERNET_DISCONNECTED 与连接失败日志，不代表成功操作。认证/重启中产生的历史浏览器日志没有当作本次最终错误。

发布扫描仍是非零：工作树中包含明确忽略的开发数据库等运行数据；只导出本次提交候选的源文件后，剩余一项既有 `tests/test_security.py` 的私网网段测试 fixture。已核对它与基底一致，是测试常量。没有关闭或削弱扫描，也没有把私有运行数据加入 Git；这不构成公开发布许可，本次没有公开发布。

## 推理基线与 M2 条件

API Contract、`assistant_http.py`、`model_server.py`、`inference_scheduler.py` 与交付基线逐字节核对：契约/路由/模型/调度未改变。仍为现有 Qwen3-VL-2B 与 Vision First，本次没有升级模型、改优先级、重构推理服务、增加 SSE、事件类型、统一时间线或 RTC 迁移。

M1 无已知阻塞项。以下事项留给 M2：

- 用户另行批准隔离 Jetson 实机测试窗口与端口，明确与正式进程共存方式、备份和回退步骤；不拿开启 unprofiled_chat 来绕过正式视觉保护。
- 准备独立数据库、模拟宝宝档案及现有 Qwen3-VL-2B 环境；核对系统时间、路径/权限与迁移；不得复制真实生活记录作测试数据。
- 核实模型服务/application 的 Assistant 配置，确保文字和视觉复用同一实例、只走回环接口；M1 未验证真实模型生成结构化输出的稳定性。
- 单独测中文多轮、抽取/确认/事实回复、繁忙/超时/取消恢复，记录 p50/p95/max 延迟、内存峰值和视觉观察间隔/缺口；先以视觉任务可持续运行为准。
- 本次未验收真实手机软键盘/Safari、真实 Qwen 抽取准确率、GPU 性能、物理摄像头或音频。正式部署、main 合并及服务重启仍需后续明确授权。

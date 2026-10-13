# 临时成人工程测试

默认宝宝 Prompt 保持不变。主应用 `BMON_PERSON_TEST_MODE=true` 时，在每次
Qwen 请求中明确选择成人 Prompt，仅把观察对象改为人（包括成人），保留字段、
unknown 规则、置信度门槛及连续确认逻辑。关闭后下次请求恢复原始宝宝 Prompt。
模型 worker 需运行支持该请求参数的版本；模式不匹配时应用拒绝采用结果。

停止现有主应用后，用 `bash scripts/run-person-test.sh` 启动。它沿用私有 `.env`
的相机/音频配置，但将 SQLite 和照片存入 `data/person-test/`，网页全局显示
`TEST MODE`，observations 的 metadata_json 记录 person_test_mode。
不要同时启动两个主应用。模型 worker 用原有 `bash scripts/run-model.sh`。
若这个测试目录已有网页保存设置，以保存的模式选择为准；可在网页开关中重新开启成人测试。

验收前确认 Jetson 系统日期正确。本人进入完整画面、睁眼，随后闭眼静卧，
观察模型输出、稳定确认后的 activity timeline 和状态记录页。保留原有采样间隔
及阈值；闭眼不保证模型判为 sleeping，也不能用成人闭眼测试证明真实睡眠准确率。
不手工写入 sleeping 来冒充模型验收。

测试结束停止测试主应用，再用 `bash scripts/run.sh` 启动；确保私有 `.env`
未启用 BMON_PERSON_TEST_MODE（默认 false）。这样恢复宝宝 Prompt 和原数据目录，
成人记录保留在独立目录。当前启动方式不配置开机自启。

空记录页回归：`node tests/test_records.cjs`。

## 网页切换观察对象

设置 →「成人测试模式」：关闭为 Infant Mode，开启为 Adult Test Mode。
共享JSON和图像规则，各自有独立主体规则，不再靠替换“宝宝”生成成人Prompt。
设置持久化和切换时重置仍保留；旧模式请求、错误及旧帧不会污染新模式。

网页开关不更换数据库。新记录持久保存 infant/adult_test 模式标签；旧数据
只有明确证据才补标，其他来源为 legacy_unknown。记录页默认婴幼儿统计，
成人与来源未知数据分别查询。照片、人工训练样本按模式分目录，历史文件不搬动。
完整边界、参数及迁移说明见 [双模式视觉说明](DUAL_MODE_VISION.md)。

过夜测试需保持 Jetson、主应用、模型服务及摄像头运行，画面能看清观察对象。
无需保持浏览器打开；置信度和连续确认满足后才记录有效状态。闭眼模拟不保证
模型输出 sleeping，未知不计入睡眠/清醒。测试不能证明婴幼儿真实睡眠准确率。

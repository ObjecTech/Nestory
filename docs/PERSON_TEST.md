# 临时成人工程测试

默认宝宝 Prompt 保持不变。主应用 `BMON_PERSON_TEST_MODE=true` 时，在每次
Qwen 请求中明确选择成人 Prompt，仅把观察对象改为人（包括成人），保留字段、
unknown 规则、置信度门槛及连续确认逻辑。关闭后下次请求恢复原始宝宝 Prompt。
模型 worker 需运行支持该请求参数的版本；模式不匹配时应用拒绝采用结果。

停止现有主应用后，用 `bash scripts/run-person-test.sh` 启动。它沿用私有 `.env`
的相机/音频配置，但将 SQLite 和照片存入 `data/person-test/`，网页全局显示
`TEST MODE`，observations 的 metadata_json 记录 person_test_mode。
不要同时启动两个主应用。模型 worker 用原有 `bash scripts/run-model.sh`。

验收前确认 Jetson 系统日期正确。本人进入完整画面、睁眼，随后闭眼静卧，
观察模型输出、稳定确认后的 activity timeline 和状态记录页。保留原有采样间隔
及阈值；闭眼不保证模型判为 sleeping，也不能用成人闭眼测试证明真实睡眠准确率。
不手工写入 sleeping 来冒充模型验收。

测试结束停止测试主应用，再用 `bash scripts/run.sh` 启动；确保私有 `.env`
未启用 BMON_PERSON_TEST_MODE（默认 false）。这样恢复宝宝 Prompt 和原数据目录，
成人记录保留在独立目录。当前启动方式不配置开机自启。

空记录页回归：`node tests/test_records.cjs`。

## 网页切换观察对象

设置 →「成人测试模式」：关闭观察宝宝，开启观察人（包括成人）。这会改变
Qwen 请求使用的 Prompt；原始宝宝 Prompt 不被修改。设置保存在当前数据目录的
`observation_subject.json`，重启后沿用。切换时原状态回到 unknown，连续确认重新
开始，旧模式尚未完成的推理结果会被丢弃。

网页开关不会更换数据库：此前记录保留，新增成人时间段标注「成人测试」，
观察 metadata 保留 `person_test_mode`。同一天切换多次时总时长包含两种模式，
不可将成人测试总量解释为宝宝数据。若需单独数据库，使用上面的启动脚本；
该目录已有网页设置时，以保存的选择为准。恢复宝宝也可直接关闭网页开关。

过夜测试需保持 Jetson、主应用、模型服务及摄像头运行，光线足以看清观察对象。
无需保持浏览器打开；只有置信度及连续确认条件满足后才记入清醒/睡眠区间，
未知状态不计入两者。视频推断不等于真实睡眠测量。

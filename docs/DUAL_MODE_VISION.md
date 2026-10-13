# 双模式视觉观察与数据边界

两种模式共用已安装的本地 Qwen3-VL-2B-Instruct 与统一 JSON 结构。
`BMON_PERSON_TEST_MODE` 保持为初始默认；网页设置保存在当前数据目录的
`observation_subject.json`，已有保存值优先。`baby_visible` 在 Infant Mode
指可确认婴幼儿，在 Adult Test Mode 指可确认测试成人。

## 观察规则

`model_prompt.py` 的共用图像/JSON规则与专用规则分别组合，不再用文字替换
把婴幼儿要求变成成人要求。Infant Mode 保留未见宝宝、身份不明、遮挡/片段
不得猜测状态等约束。Adult Test Mode 允许明确躺卧时测试仰卧/侧卧/俯卧；
坐姿、站立、行走、仅见头脸的卧姿为 unknown；仅闭眼静止或人工模拟不能
证明真实睡眠，不强迫输出 sleeping。人工状态设置仅用于工程流程验证。
灰度/近红外按相同可见性规则处理，没有真实夜视准确率验收结论。

## 调度、稳定性与过期

- 摄像头连续采集，图像运动采样周期仍为 0.8 秒；空间差分去除整体曝光/IR
  亮度变化，最近三次运动采样的中位数抑制单帧波动。
- 轻量分析仍用 `analysis_interval_seconds`，沿用睡眠至少5秒、运动最多1.5秒
  的现有机制。Qwen 主判时，轻量结果仅参考，不再自动改状态或保存笑脸。
- Qwen 使用一个观察线程同步请求；推理在模式锁外，不阻塞采集，不建立队列。
  默认普通30秒、睡眠/不在画面60秒、明显运动15秒；离线重试间隔仍为5秒。
- 轻量状态仍为至少3次连续确认；睡眠30秒、清醒10秒、睡姿20秒。
  Qwen 的 ObservationFilter 仍为至少2次、`vlm_stable_seconds=20`秒。
- 原默认阈值保持：sleep_motion_threshold=2.5、motion_threshold=8、
  vlm_confidence_threshold=0.60；Qwen 原活动门槛0.75、卧姿门槛0.82、
  表情门槛0.78继续保留。camera_fps=15、analysis_interval_seconds=3。
- Qwen 默认超时35秒；本次读到 Jetson 私有覆盖60秒，未改写 `.env`。
  `Config.from_env()` 的原环境变量覆盖仍有效。
- last_analysis_ts 是请求/尝试时间，last_success_ts 是成功时间，失败不会
  覆盖最后成功结果。Qwen结果的过期窗口取现有最大观察间隔+超时；
  摄像头离线或画面停更时页面显示未知/过期，不把旧结果当成当前判断。

切换在共享锁内完成，重置两条链路的候选、表情票数、临时结果、去重缓存和
成功时间。连续无法确认时也使用原Qwen稳定时间和最少观察次数回到unknown，
不会无限沿用旧的睡眠/卧姿。旧请求和旧错误通过 generation 丢弃，切换前采集的帧不能写入新模式。
两种模式使用同一套间隔与阈值，不增设成人演示专用时间参数。

## SQLite 增量迁移与媒体

intervals、observations、events 各新增 observation_mode 列，值为 infant、
adult_test 或 legacy_unknown。切换时关闭旧模式开放区间并新建未知区间，
即使前后值同为 unknown 也不跨模式合并。新观察/事件元数据同时保留模式和
兼容布尔值 person_test_mode。

旧 observations/events 仅在元数据明确给出模式或布尔值时补标；明确的
`[成人测试]` 区间补标 adult_test。缺失、损坏或矛盾的来源保留 legacy_unknown。
不根据当前模式、文件夹叫 camera-check 或邻近的一条观察给整批历史定性。
迁移不删除记录、不改已有媒体路径。数据库首次升级前建议用 SQLite backup
接口保存一致副本，不用运行中直接复制单个 .db 忽略 WAL。

现有 `/api/timeline` 与 `/api/observations` 接受 `mode`，默认 infant；
查询一日或更长时间范围均不会合并成人/未知数据。记录页默认婴幼儿记录，
可切换查看成人或历史模式未知；照片列表的标签依据记录自身模式，和当前开关无关。
本次没有新增睡眠日报/周报功能。

新媒体保存到 classified/<mode>/…、smiles/<mode>/…；人工训练样本保存到
training/<mode>/<category>/<value>/，同名JSON保留模式、人工标签和采集时间。
旧 training/activity 或 training/posture 等目录保持原样，来源尚未确认，
不能直接作为婴幼儿专用训练数据导入。模式切换后的旧帧保存请求会被拒绝。

## 可选 posture.onnx 与验收

轻量睡姿模型保持原可选五类接口，与 Qwen三类卧姿及unknown独立。
不存在或输入/输出不兼容时报告不可用，不下载、不生成权重，不阻止Qwen运行。
成人使用轻量模型不能证明它适合婴幼儿。本次不重新训练。

`python3 -m unittest discover -s tests` 与 `node tests/test_records.cjs` 覆盖
模式往返、在途成功/超时/坏JSON、旧帧、人工样本、媒体标记、旧库迁移、
统计过滤、灰度曝光变化、原调度及阈值、可选模型缺失等工程行为。
测试图片为临时合成，不提交私人图片。代码同步不等于运行进程已加载新版本；
按用户要求，真实服务重启须单独确认。

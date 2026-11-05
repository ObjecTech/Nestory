# 一次性维护工具

这些脚本用于 2026-10 Assistant 阶段的 Jetson 实机验收和手工切换，正在运行的应用不会导入它们。

| 文件 | 用途 |
| --- | --- |
| `assistant-maintenance.py` | 旧的 nohup 切换/恢复工具（已被 `docs/DEPLOY.md` 的 systemd 流程取代） |
| `run-assistant-qwen-test.py` + `assistant_test_harness.py` + `assistant_test_cases.py` | 真实 Qwen 单实例验收测试 |
| `backup-assistant-maintenance.py` + `assistant_test_backup.py` | 维护前备份 SQLite |
| `check-assistant-maintenance-stop.py` | 停服前的只读预检 |

从仓库根目录运行，例如 `python3 -B maintenance/run-assistant-qwen-test.py --help`。

# Jetson 部署与开机自启

2026-10-18 起，Jetson 上的模型服务和网页应用由两个 **systemd 用户服务**管理，
断电或重启后自动启动（用户 linger 已开启，无需登录）。

| 服务 | 作用 | 监听 |
| --- | --- | --- |
| `nestory-model` | 本地 Qwen3-VL-2B（视觉 + 聊天） | 仅回环 `127.0.0.1:8010` |
| `nestory-app` | 网页应用 | 回环 `:8080` 及 `.env` 里配置的 LAN 地址 |

## 目录约定

| 位置 | 内容 |
| --- | --- |
| `~/baby-monitor-public` | Git 工作副本，同时存放私有 `.env`、`data/`、`models/`、`.venv-*` |
| `~/nestory-releases/<名称>-<提交>` | 某个提交的只读代码快照（`git archive` 导出） |
| `~/nestory-releases/current` | 指向正在运行的快照的软链接 |

服务通过 `current/scripts/start-release.sh model|app` 启动：代码取自快照，
配置、数据、模型和虚拟环境取自工作副本。这样升级代码不会动到数据和配置，
回退只需把软链接指回旧快照。

## 发布新版本

在 Jetson 上（提交已推送到共享仓库后）：

```bash
sha=<要发布的提交>
dir=~/nestory-releases/<名称>-$sha
mkdir "$dir" && git --git-dir ~/repos/baby-monitor-public.git archive "$sha" | tar -x -C "$dir"
previous=$(readlink -f ~/nestory-releases/current)
ln -sfn "$dir" ~/nestory-releases/current
systemctl --user restart nestory-model nestory-app
```

只改了网页或后端、没动 `model_server.py` 时，可以只重启 `nestory-app`，省去约 15 秒模型加载。

## 回退

```bash
ln -sfn "$previous" ~/nestory-releases/current
systemctl --user restart nestory-model nestory-app
```

## 常用命令

```bash
systemctl --user status nestory-model nestory-app
journalctl --user -u nestory-app -n 50
curl -s http://127.0.0.1:8010/health
```

## 首次安装（已完成，留作记录）

```bash
mkdir -p ~/.config/systemd/user
cp deploy/nestory-model.service deploy/nestory-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable nestory-model nestory-app
loginctl enable-linger "$USER"
```

模型服务用 SIGINT 正常退出（`KillSignal=SIGINT`，最多等 120 秒），不会被强杀。
之前手工切换用的 `maintenance/assistant-maintenance.py` 只用于读取旧的恢复清单，
新发布不再需要。

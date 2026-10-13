# Mac 与 Jetson 的 Git 工作流

当前共享仓库放在 Jetson，通过已有的 SSH 别名 `jetson` 访问。
不依赖 GitHub，也不需要连接摄像头、音箱或麦克风。

## 三个目录的职责

| 位置 | 目录或地址 | 用途 |
| --- | --- | --- |
| Mac | 本地 `baby-monitor-public` 项目目录 | 日常编辑、测试、提交 |
| Jetson | `~/baby-monitor-public` | 可编辑、可提交的工作副本，之后用于硬件调试 |
| Jetson | `~/repos/baby-monitor-public.git` | 共享裸仓库，只存版本历史，不在这里编辑文件 |

两端使用 `main` 分支。Mac 的 `origin` 是
`jetson:repos/baby-monitor-public.git`；Jetson 工作副本的 `origin`
指向本机共享裸仓库。共享仓库拒绝非快进推送及分支删除。
Git 身份只在本项目配置，不修改其他项目的全局身份。

保存文件会立即表现为当前工作副本的改动，但不会自动提交、推送或更新另一端。
完成一个可验证的修改后再提交并同步；没有后台自动提交程序。

## 在 Mac 修改

在项目目录中，开始修改前先运行：

```bash
git pull --ff-only
```

修改并验证后：

```bash
git status
git diff
git add <本次修改的文件>
git commit -m "Describe the completed change"
bash scripts/sync-jetson.sh
```

同步脚本先检查两端状态，拉取 Jetson 已发布的提交，推送 Mac 提交，
然后更新 Jetson 工作副本，并比较两端提交 ID。
它要求两端处于 `main` 且工作区干净；Jetson 的本地提交必须先推送。
遇到分叉历史、未提交文件、SSH 断线或异常配置时停止，不强推或清除文件。
若已推送但工作副本更新失败，脚本会明确报告，可处理后重试。

脚本仅同步源代码，不安装依赖、不重启运行中的服务。

## 在 Jetson 修改

```bash
ssh jetson
cd ~/baby-monitor-public
git pull --ff-only

# 编辑代码并验证
git status
git diff
git add <本次修改的文件>
git commit -m "Describe the Jetson change"
git push
```

回到 Mac 项目目录执行 `git pull --ff-only`，即可取得 Jetson 的修改。
Remote SSH 编辑器应打开 `~/baby-monitor-public`，不要打开裸仓库。

切换开发机器前，先在原机器提交并推送；在新机器拉取后再编辑。
如果两边已经分别提交而形成分叉，保留两边提交，检查后显式合并，
不要使用 `reset --hard` 或强制推送来覆盖另一边。

## 检查同步状态

Mac 项目目录：

```bash
git status --short --branch
git log -3 --oneline
git ls-remote origin refs/heads/main
ssh jetson 'git -C ~/baby-monitor-public status --short --branch; git -C ~/baby-monitor-public log -3 --oneline'
```

Mac、Jetson 工作副本及共享仓库的 `main` 提交 ID 应一致。
Git 仓库中的 `.env`、虚拟环境、权重、照片、数据库和缓存均被忽略；
这些运行配置和数据需要各自准备或另行备份。

## 公开 GitHub 发布工作流 (Public Release)

公开仓库位于：`https://github.com/ObjecTech/Nestory`

该仓库向公众开源，必须严格保证不泄露任何私有网络拓扑、账号密码、模型权重、运行数据库及私人媒体文件：
1. **脱敏发布机制**：发布由辅助工程师（Gemini）独立负责，推送前必须运行 `python3 scripts/scan-release.py` 执行安全扫描。
2. **独立发布历史**：GitHub 公开分支作为脱敏发布目标，其提交历史与 SHA 允许独立于内部开发仓库，发布时采用干净的脱敏工作树提交，严禁强制推送（`--force`）。
3. **日常同步边界**：Mac 与 Jetson 内部日常开发仍严格使用 `bash scripts/sync-jetson.sh` 保持 SSH 裸仓库原子同步，不直接从开发环境自动推送到外部 GitHub。

## 初次双向验证记录

2026-09-11：Mac 首次提交及工作流文档已通过 SSH 推送，并成功更新
Jetson 工作副本。本节直接在 Jetson 编辑、提交并推送，用于验证 Mac
能够拉回远程端产生的真实文件改动。另已验证：Jetson 存在未提交文件时，
Mac 同步脚本会拒绝更新并保留该文件。该验证不涉及硬件或应用服务启动。

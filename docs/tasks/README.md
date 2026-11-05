# 任务书（交给其他 Agent 的低复杂度任务）

Claude Code 负责规划、审查和合并。适合交出去的任务写成本目录下的任务书，由用户转交给
Codex 或 Antigravity（Gemini 3.8 Flash）。Agent 之间不直接通信。

## 分工

| Agent | 适合的任务 |
| --- | --- |
| Claude Code | 规划与拆分任务、审查合并、契约/数据库/推理调度、Jetson 部署与实机验收 |
| Codex | 边界清楚的后端小功能或 bug 修复、补测试、脚本 |
| Antigravity（Gemini 3.8 Flash） | 前端界面与文案、样式、文档、演示材料 |

以下内容不交出去：API 契约、数据库结构与迁移、推理调度、模型服务、Jetson 部署与服务重启、
`.env` 和任何私有数据。

## 流程

1. Claude 写任务书 `docs/tasks/<agent>-<任务名>.md`（`<agent>` 为 `codex` 或 `antigravity`），提交到 `main`。
2. 用户把任务书路径交给对应 Agent。
3. Agent 从最新 `main` 拉出分支 `<agent>/<任务名>`，只改任务书允许的文件，
   跑完任务书里的验收命令，把分支推送到 `origin`。不合并 `main`，不运行 `scripts/sync-jetson.sh`，
   不登录或重启 Jetson 服务。
   如果和 Claude 共用同一个项目文件夹，推送后用 `git switch main` 切回 main，不要把工作目录停在自己的分支上。
4. Claude 审查分支，合并到 `main`，按 `docs/DEPLOY.md` 上线，并把任务书移到 `done/`。

## 任务书模板

```markdown
# 任务：<一句话标题>（Codex | Antigravity）

基线：`main` 的 <提交>。分支：`<agent>/<任务名>`。完成后推送该分支，由 Claude 审查合并。

## 背景
<为什么做，用户看到的现象>

## 目标
1. <可验证的结果>

## 允许修改的文件
- <路径>

## 验收标准
- <用户能看到/操作的结果>
- 命令：`python -B -m unittest discover -s tests` 全部通过；涉及前端时 `node tests/<相关>.cjs` 通过

## 禁止事项
- 不改上面列表以外的文件；不改 API 契约、数据库、模型服务；不碰 Jetson
- 失败时界面如实显示失败，不用示例数据或 Mock 冒充成功
- 不复制受版权保护的文字、歌词或图片
```

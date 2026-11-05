# Project collaboration

- Read `docs/GIT_WORKFLOW.md` before changing Git remotes or synchronizing machines.
- Read `docs/ASSISTANT_ARCHITECTURE_DECISIONS.md` before changing the AI Assistant, model services, the database layer, clock handling or network behavior. Only ADRs with status Accepted are binding; agents may add Proposed entries, but only the user can accept them. Changing the model, service topology, data boundary or outbound networking requires the user's approval first.
- Before committing, run `python -B -m unittest discover -s tests -v` and `python -B scripts/scan-release.py`.
- **Multi-Agent Collaboration Roles**:
  - **Claude Code**: 主力开发与总负责。规划并拆分任务、审查合并、API 契约/数据库/推理调度/模型服务、Jetson 部署与服务重启、硬件验收及内部 Mac-Jetson 同步（`bash scripts/sync-jetson.sh`）。
  - **Codex**: 按 `docs/tasks/codex-*.md` 任务书完成边界清楚的后端小功能、bug 修复与测试，在 `codex/<任务名>` 分支提交并推送，由 Claude 审查合并。
  - **Antigravity（Gemini 3.8 Flash）**: 按 `docs/tasks/antigravity-*.md` 任务书完成前端界面、文案、文档与演示材料，在 `antigravity/<任务名>` 分支提交并推送，由 Claude 审查合并；负责脱敏发布至公开 GitHub。
  - 协作流程与任务书模板见 `docs/tasks/README.md`。Codex 与 Antigravity 不合并 `main`、不运行同步脚本、不重启 Jetson 服务。
  - **ChatGPT**: 需求规划与功能验收标准制定。
- The user wants completed code changes versioned and synchronized between the Mac
  and Jetson. After validating a requested change, commit its relevant files and
  push to the shared repository. On the Mac main branch, use
  `bash scripts/sync-jetson.sh` to also update and verify the Jetson working copy.
  On the Jetson, commit and push from `~/baby-monitor-public`.
- Inspect the working tree before edits. Preserve unrelated user changes; never
  automatically stage unrelated files, discard work, or force-push to reconcile
  diverged histories. Report blocked synchronization accurately.
- Saving a file alone does not commit or synchronize it. No background watcher
  or automatic service restart is configured.
- Keep private environment files, model weights, media, databases and virtual
  environments out of Git. Hardware setup is separate from source synchronization.

# Project collaboration

- Read `docs/GIT_WORKFLOW.md` before changing Git remotes or synchronizing machines.
- **Multi-Agent Collaboration Roles**:
  - **Codex**: 主力开发工程师。负责 Jetson 系统服务部署、服务重启与硬件验收、后端 API 开发及内部 Mac-Jetson SSH 同步（`bash scripts/sync-jetson.sh`）。
  - **Gemini**: 辅助开发与独立审查负责人。负责工程架构质量审查、潜在缺陷推演、前端 UI / 文档维护、以及脱敏发布至公开 GitHub。在只读审查期间不擅自重启运行中的硬件服务。
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

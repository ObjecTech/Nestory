# Project collaboration

- Read `docs/GIT_WORKFLOW.md` before changing Git remotes or synchronizing machines.
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

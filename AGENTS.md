# AGENTS.md

Primary goal: minimize task wall-clock time while maintaining correctness.

1. For complex tasks, quickly split work into independent sub-tasks.
2. Run independent investigation tasks in parallel with subagents.
3. Prefer 2–4 focused subagents; do not maximize agent count.
4. Give each subagent a narrow scope and clear output.
5. Do not let multiple agents scan the entire repository.
6. Start from the most likely files/modules and expand only if needed.
7. Avoid reading unrelated folders such as node_modules, dist, build, and generated files.
8. Use subagents mainly for code search, root-cause analysis, tests, logs, and review.
9. The main agent coordinates, synthesizes findings, and decides the final implementation.
10. Do not let multiple agents modify overlapping files in the same working tree.
11. If independent agents must edit code concurrently, use separate Git worktrees.
12. Prefer the smallest correct code change; avoid unrelated refactors.
13. Run targeted tests first; run the full test suite only when necessary.
14. Run independent tests/lint/checks in parallel when possible.
15. Avoid repeated repository exploration or rereading already-understood files.
16. Stop investigating once there is enough evidence to implement safely.
17. Do not spend excessive time planning; start execution quickly.
18. For tiny tasks, use one agent; parallelism is only useful when it reduces completion time.
19. If a strategy fails twice without new evidence, change the hypothesis instead of retrying.
20. Optimize for the fastest correct, tested result—not exhaustive analysis.

# 项目工作规则

## 修改代码后重新打包

- 每次涉及代码修改（包括界面、文案、样式和资源）的任务，完成修改和必要检查后，必须重新打包，不能只交付源码修改。
- 使用项目虚拟环境运行 `.venv/bin/python packaging/build.py`，再运行 `.venv/bin/python packaging/verify_frozen.py` 验证打包产物，并运行 `.venv/bin/python packaging/archive.py` 生成分发压缩包。
- 默认构建当前机器的 macOS Apple 芯片版本，产物放在 `dist/`；用户明确指定其他平台时遵循用户要求。
- 最终回复提供最新压缩包的可点击路径；如打包或验证失败，应修复并重试，仍无法完成时如实说明原因。
- 用户已授权此流程，后续代码修改完成后无需再次询问是否重新打包。

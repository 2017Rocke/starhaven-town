# 可选团队数据

小镇可在未连接任何团队时使用。团队接入后只读取台账，不启动工作智能体。

选择一个目录，放置 `config.json` 和 `state.json`。最小配置与台账分别为：

```json
{"automatic_execution_enabled": false, "members": [{"id": "scout", "display_name": "星探"}]}
```

```json
{"work_items": [], "improvements": [], "weekly_plans": [], "handoffs": []}
```

工作项支持 `owner_id` / `member_id`、`goal` / `title`、`status` 等字段；读取规则以 `server.py` 的 `records`、`decorate` 与 `build_prompt` 为准。能力改进只有具备基线、候选结果、复核人与通过的回归检查时才计入已验证数量。

研究报告使用 `YYYY-MM-DD/report.html`，可有 `review.json` 摘要及 `assets/` 图片。仅展示固定报告入口与图片路径，HTML 使用 sandbox 策略，不执行报告脚本。

桌面菜单选择团队目录后，路径记入本机 `settings.json` 的 `source_dir` 与 `report_root`。请勿把真实团队资料提交到公共项目。

# 跨电脑迁移小镇状态

版本：`2.0.1` 起可用。工具：`scripts/state_bundle.py`。快照目录：`state/`。

一台电脑上训练出来的成果、技能、预算账务和两署档案，都可以打包带到另一台电脑继续，不必重新训练。本文是该流程的权威说明。

## 为什么不能直接提交 data 目录

仓库的 `.gitignore` 明确排除 `data/`、`*.sqlite3*`、`provider.json`、`settings.json`、`*.log`。原因是数据目录里有**聊天正文**，而这是一个公开仓库，一旦提交就进了不可撤销的公开历史；另外 git 对二进制数据库不友好，每次提交都存整份新 blob。

所以迁移用的是**打包后的快照**，并要求默认移除聊天正文。

## 一个快照包含什么

| 内容 | 是否计入 |
| --- | --- |
| `town.sqlite3`（居民、训练任务、成果记录、技能、预算、账务） | 是 |
| `workspaces/<居民>/` 下的成果正文文件 | 是 |
| `scout-workshop/workspace.sqlite3`（星探采集索引） | 是 |
| `security-office/security.sqlite3`（两署只追加档案） | 是 |
| `provider.json` 的 `mode`、`base_url`、`model` | 是 |
| 聊天正文（`messages`、`v2_message_sources`） | **否**，`--strip-chat` 移除 |
| 模型密钥 | **否**，不进文件 |
| `scout-workshop/materials/`（约 65 MB 第三方源码） | 否，需要时加 `--include-materials` |
| `WebView2/`、`logs/`、`backups/`、`runtime/`、`town.lock`、`settings.json` | 否 |
| SQLite 边车文件 `-wal` / `-shm` / `-journal` | 否 |

导出的数据库通过 SQLite 的 backup API 复制，再 `VACUUM` 并切回 `journal_mode=DELETE`。这样即使导出时小镇仍在运行，已提交的记录也不会留在未打包的 `-wal` 里；恢复时也不会因为残留的 `-wal` 把已移除的聊天行带回来。

## 导出

```bash
python scripts/state_bundle.py export --data <数据目录> --out state/town-state.zip --strip-chat
```

- 应用**完全退出**时导出最稳妥；工具会用小镇自己的实例锁检测，发现仍在运行就拒绝。
- 确实要在运行中导出时加 `--allow-running`：数据库副本仍是一致的，但成果文件可能正在写入。
- 输出会打印文件数、体积、记录条数，以及所有被丢弃的 `provider.json` 字段。

## 提交

```bash
git add state/town-state.zip
git commit -m "chore: 更新小镇状态快照"
git push
```

约定：

- `state/` 下**只保留一个**快照文件，名字固定为 `town-state.zip`。
- 需要旧版本时用 `git log -- state/town-state.zip` 找历史提交，不要再放第二份文件。
- 快照不参与源码导出（`scripts/export_source.py` 只收录 `FILES` 与固定目录），所以不会进入公开发布的源码包。

## 恢复

```bash
git clone https://github.com/2017Rocke/starhaven-town.git
cd starhaven-town
python scripts/state_bundle.py inspect state/town-state.zip     # 先看清楚里面是什么
python scripts/state_bundle.py restore state/town-state.zip --data <安装目录>/data
```

- 恢复会逐文件校验 sha256；包内文件被改动过就拒绝并指出是哪一个。
- 目标目录已有 `town.sqlite3` 或 `workspaces/` 时默认拒绝覆盖，确认要覆盖请加 `--force`（建议先自行备份）。
- 恢复完成后**必须重新填写模型密钥**：密钥保存在操作系统的凭据管理器里，按数据目录路径命名，不随快照迁移。

## 安全边界

- 导出会扫描 `.md`、`.txt`、`.json`、`.html`、`.csv` 中的高置信度凭据特征（`sk-`、`ghp_`、`AKIA`、私钥头等），命中会写进清单的 `redactions` 字段。
- `provider.json` 只保留三个字段，其余全部丢弃并记录。
- `--strip-chat` 只删聊天正文，**不删**居民资料、训练记录、成果、账务和两署档案；这些正是迁移需要的部分。
- 快照仍然包含你的端点地址、模型名、居民设定与成果内容。放进公开仓库前请自行确认这些内容可以公开。

## 相关文件

| 文件 | 作用 |
| --- | --- |
| `scripts/state_bundle.py` | `export` / `inspect` / `restore` 三个子命令 |
| `tests/test_state_bundle.py` | 密钥不外泄、聊天移除、恢复校验、拒绝覆盖 |
| `state/README.md` | 快照目录的约定 |

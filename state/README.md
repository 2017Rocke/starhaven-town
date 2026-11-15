# state/ · 小镇状态快照

这个目录只放**一个**文件：

```
town-state.zip   最新一次可分享的小镇状态快照
```

它由 `scripts/state_bundle.py` 生成，用途是：在一台电脑上训练、产出成果之后，把状态带到另一台电脑继续，而不必重新训练。

## 里面有什么

| 内容 | 说明 |
|---|---|
| `town.sqlite3` | 居民、训练任务、成果记录、技能、预算账务、两署档案索引 |
| `provider.json` | 端点与模型名（**没有任何密钥**） |
| `workspaces/<id>/` | 成果正文文件（数据库只存哈希，正文在这里） |
| `scout-workshop/workspace.sqlite3` | 星探的采集索引 |
| `security-office/security.sqlite3` | 警察署与稽查署的只追加档案 |

## 里面没有什么

- **聊天正文**：导出时用 `--strip-chat` 移除（`messages`、`v2_message_sources`），所以这个仓库里不会出现你和居民的对话。
- **模型密钥**：密钥保存在操作系统的凭据管理器里，按数据目录路径命名，不随文件迁移。换电脑必须重新填写。
- 浏览器缓存、日志、升级备份、`town.lock`、以及星探下载的约 65 MB 第三方源码。

## 怎么用

```bash
# 1) 换电脑前，在本机导出（应用必须完全退出）
python scripts/state_bundle.py export --data <数据目录> --out state/town-state.zip --strip-chat

# 2) 提交并推送
git add state/town-state.zip && git commit -m "chore: 更新小镇状态快照"

# 3) 新电脑拉取后恢复
git clone ...
python scripts/state_bundle.py restore state/town-state.zip --data <安装目录>/data

# 4) 打开小镇，重新填写模型密钥
```

只有要迁移时才需要提交这个文件；日常开发不必动它。完整说明见 [docs/STATE_BUNDLE.md](../docs/STATE_BUNDLE.md)。

## 为什么只有一个文件、固定名字

git 对二进制文件不友好：每次提交都会存一份全新的 blob。固定文件名意味着每次只覆盖，不积累历史副本；需要历史时用 `git log -- state/town-state.zip` 取旧提交里的版本。

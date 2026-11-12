# 项目知识接入

Basic Memory 为本项目提供本地知识读写、检索和 Codex 会话 hooks。知识 Markdown 位于当前 worktree 的 `docs/knowledge/`，随 Git 审查和合并；数据库和模型属于本机缓存。人工开发、构建、测试和 CI 不依赖它。

## 安装与启动

按项目要求启用 Node 与 pnpm，并先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)。启动器支持 PATH 中的 uv，也支持 `~/.cache/finevent-memory/bin/uv`（Windows 为 `uv.exe`）。

项目标识为 `finevent`，配置见 `.codex/basic-memory.json`。

在仓库根目录执行：

```sh
pnpm knowledge:setup
pnpm knowledge:doctor
```

`setup` 使用 uv 安装隔离的 Python 3.12 和 Basic Memory 0.24.1，建立当前项目索引，并检查 MCP 和 SessionStart。首次运行需要联网下载运行时、依赖及本地向量模型；之后正常启动不执行安装或自动升级，不需要 Basic Memory 云账号。

`doctor` 检查目录绑定、运行时版本、真实 MCP 握手与项目列表，以及官方 SessionStart 上下文。它不写业务知识，会创建本地诊断用生命周期事件。它验证的是组件；Codex 是否信任并实际加载项目 hooks，还需在新会话中确认。

在 Codex 中打开或重新开始本仓库的会话，信任项目配置，并在 `/hooks` 中审查和信任仓库的 SessionStart、PreCompact 命令。核对 `basic-memory` MCP 可用且收到 Basic Memory 启动上下文。当前已经开始的会话不会自动重新加载新增配置。子目录启动也受支持。

TODO: 本次已验证真实 MCP、SessionStart 和 PreCompact 命令，仍需在新会话中确认客户端已信任并实际加载项目配置；取得这部分客户端验证后删除本项。

## worktree 绑定

[公共启动器](../scripts/knowledge.mjs)供 MCP、hooks 和手工命令共用。每次启动会定位并校验当前 Git worktree，将唯一的本地项目 `finevent` 映射到该 worktree 的 `docs/knowledge`。MCP 与 hooks 都通过 Git 根目录定位启动器，不依赖 MCP 配置中相对 `cwd` 的解析。新 worktree 自动建立自己的配置及数据库；同一用户可复用已安装的运行时和模型。

默认本机目录为 `~/.cache/finevent-memory/`：

- `runtime-0.24.1/`：隔离 Python 与 Basic Memory。
- `models/`、`huggingface/`、`uv-cache/`、`python/`：模型、包和 Python 缓存。
- `worktrees/<路径哈希>/`：该 worktree 的绑定记录、Basic Memory 配置、数据库及生命周期事件。

可用 `FINEVENT_MEMORY_CACHE` 指定缓存根目录；MCP、hooks 和安装命令必须使用相同值。缓存路径应保持较短，避免 Windows 模型下载路径过长。

运行时不信任外部传入的 `BASIC_MEMORY_CONFIG_DIR`，而是根据当前 worktree 重新确定它。复制来的配置若指向其他目录，或知识目录通过链接逃逸到 worktree 外，会报错并停止；不会自动改写未知配置。先按报错路径检查对应缓存，确认属于当前 worktree 后重建该单个缓存，不要复制数据库给其他 worktree。

这些是路径和配置校验，不是新的操作系统沙箱。调用工具时仍沿用 Codex 的权限机制。

## 日常操作

| 命令                                             | 用途                                               |
| ------------------------------------------------ | -------------------------------------------------- |
| `pnpm knowledge:setup`                           | 安装或恢复固定版本运行时，索引当前知识并做启动检查 |
| `pnpm knowledge:doctor`                          | 检查当前 worktree 的绑定及 MCP、SessionStart       |
| `pnpm knowledge:cli project index finevent`      | Git 合并或手工修改笔记后，按需重建当前项目索引     |
| `pnpm knowledge:cli hook status --harness codex` | 查看当前 worktree 的生命周期事件状态               |
| `pnpm knowledge:cli hook flush`                  | 将本地生命周期事件归档；不把事件自动变成知识笔记   |

CLI 操作通过 `knowledge:cli` 进入同一绑定环境，不直接使用全局 `bm`。不要同时启用上游全局插件的默认 MCP/hooks，否则会出现第二个未绑定本 worktree 的入口。

写入 `docs/knowledge/` 的笔记使用仓库相对路径。交接保留仓库、分支、提交和 PR，省略本机绝对路径、操作系统用户名、主机名和聊天会话或轮次标识；这条约定同时适用于元数据、正文和链接。恢复使用笔记的 permalink，已知前序笔记时可用 `continues` 关联。

项目 MCP 和 `knowledge:cli` 在 Basic Memory 接受新建、覆盖、编辑或移动后的完整 Markdown 前校验内容。拒绝写入后可修正原请求重试。更新启动器后需重启当前项目的 MCP 进程才能启用新校验。

升级 Basic Memory 或修改写入流程时，用已安装的项目运行时 Python 执行 [MCP 写入回归](../tests/knowledge_runtime_integration.py)，核对拒绝写入、原文保留和修正后重试。

手工或其他工具修改的笔记由 Lefthook 在自动修复前检查暂存内容，修复、重新暂存后再检查最终版本。可运行 `node scripts/knowledge-review.mjs staged` 单独复核。检查失败时修正笔记并重新暂存。

项目技能负责按任务收录决策、读取知识和创建 checkpoint。现有 README、AGENTS.md、DESIGN.md 和源码继续作为各自内容的维护入口；知识笔记引用相关来源，避免重复保存配置数值。Markdown 进入普通 diff、格式检查和 PR 审查，数据库与缓存不提交。

TODO: 当前已提供变更候选检查和中文字面检索回退，但任意来源的历史代码变化不会无人值守地修复所有知识，中文语义召回质量也未作专项验证。真实试点需验证任务中的收录和维护可靠性，再决定是否需要更强的漂移检测或检索方案；读取时仍核对来源与适用范围。

## 开发中的知识维护

[project-knowledge](../.agents/skills/project-knowledge/SKILL.md) 由 AGENTS.md 接入正常开发：开始时按主题检索，收尾时检查变更影响，按需调用官方技能收录或修订。普通小改动没有持久知识时不生成笔记；只读问答不修改知识。

当前参考笔记使用 frontmatter 的 `sources` 数组关联来源文件，例如：

```yaml
sources:
  - src/export-policy.mjs
  - docs/export-policy.md
```

路径相对仓库根目录，指向精确文件。通过 MCP 写笔记时可传 `metadata.sources`。既有 README、AGENTS.md、DESIGN.md 仍在原处维护，笔记只保留必要背景和指针。

两个只读辅助命令不调用模型，也不依赖 Basic Memory 运行时；需要已安装项目开发依赖：

```sh
pnpm knowledge:check
pnpm knowledge:check --since <任务开始或合并前的提交SHA>
pnpm knowledge:find 超时 重试 --limit 5
```

`check` 默认检查当前暂存、未暂存和未跟踪变化；指定 `--since` 后再加入该提交到 HEAD 的差异。重命名包含旧、新路径，来源文件已不存在时也会提示。`candidates` 是需要判断的笔记，`unlinked` 是未建立来源关联的笔记；schema、历史 checkpoint 和已废止决策不作为当前知识维护对象。命令不会改写笔记、批准提议或记录“已审查”状态。

`find` 在当前仓库的知识 Markdown 中做 Unicode 字面包含匹配，多个参数按任一词命中，不解析全文检索的布尔语法。仅返回标题、标识、状态、来源和短摘录；选中候选后再用 MCP 或读取对应文件获取正文。它是有限检索回退，不能替代语义检索。

两者默认每组返回 10 项，可用 `--limit`（1–50）和 `--offset` 分页，输出包含总数和下一页位置。未解析的笔记、非法来源或超过 256 KiB 的文件会进入 `warnings`，并使 `complete=false`。含本机路径、机器或会话字段、聊天链接的笔记也会报告警告，单测核对仓库中的笔记。没有候选也不代表所有知识都已核实：检查只覆盖指定变化范围与已记录的来源关系。

知识文件沿用现有格式检查、Git diff 和 PR 审查。当前规则变化时维护现行笔记或建立替代关系，checkpoint 保留当时状态；文档合并不等于方案批准。

## 接入方式与版本

采用 Codex 项目级 MCP 和 hooks 配置，并由启动器调用官方 `basic-memory mcp` 与 `basic-memory hook`。上游全局插件的默认命令没有本项目的 worktree 绑定，因此这里没有执行用户级 `bm install codex`，也没有修改上游插件源码。

五个官方技能 `bm-orient`、`bm-decide`、`bm-writing`、`bm-remember`、`bm-checkpoint` 来自 [Basic Memory 固定提交](https://github.com/basicmachines-co/basic-memory/tree/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/plugins/codex/skills)，其中交接和恢复技能按项目的隐私约定适配，各目录保留上游许可证原文（上游声明为 `AGPL-3.0-or-later`）。Oxfmt 只对这五个第三方目录增加例外；项目自身的脚本、配置和文档仍正常检查。schema 基于同版本官方模板，作为项目可维护的笔记格式；其许可证、来源及项目修改记录见 [第三方材料说明](../THIRD_PARTY_NOTICES.md)。

2026-11-05 从 vue-baseline 的 `b8564eb` 接入，使用 Basic Memory 0.24.1 和 Python 3.12。Basic Memory 的包版本固定，传递依赖由安装时解析；升级应作为单独工程变更，审查上游技能及运行时变化并复验绑定、失败路径、MCP 和 hooks。

当前包括可靠接入及项目内的按需收录、变更检查和检索回退。长期中文检索质量、真实需求中的知识维护收益及模型成本仍需在后续试点中观察。

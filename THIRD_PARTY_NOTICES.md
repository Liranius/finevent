# 第三方材料与许可范围

本仓库自有代码适用 [MIT 许可证](LICENSE)；直接收录的第三方材料遵循下列许可证。包管理器安装的依赖遵循各自许可证。

## Basic Memory 官方技能

来源：[Basic Machines / Basic Memory](https://github.com/basicmachines-co/basic-memory/blob/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/plugins/codex/skills)，固定提交 `1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356`。上游在 [pyproject.toml](https://github.com/basicmachines-co/basic-memory/blob/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/pyproject.toml) 中声明 `AGPL-3.0-or-later`。

范围为下列五个目录的上游文件，各附许可证原文：

- [.agents/skills/bm-orient/](.agents/skills/bm-orient/LICENSE)
- [.agents/skills/bm-decide/](.agents/skills/bm-decide/LICENSE)
- [.agents/skills/bm-writing/](.agents/skills/bm-writing/LICENSE)
- [.agents/skills/bm-remember/](.agents/skills/bm-remember/LICENSE)
- [.agents/skills/bm-checkpoint/](.agents/skills/bm-checkpoint/LICENSE)

2026-11-05 从 vue-baseline 的 `b8564eb` 引入相同上游原文件，仅附加 LICENSE 副本。2026-11-11 项目修改了 `bm-checkpoint/SKILL.md` 和 `bm-orient/SKILL.md`，使交接与恢复使用仓库标识和笔记链接，省略工作站与会话标识。

## Basic Memory 派生 schema

[docs/knowledge/schemas/](docs/knowledge/schemas/) 中的 `coding-session.md`、`decision.md` 和 `task.md` 派生自同一固定提交的 [plugins/codex/schemas/](https://github.com/basicmachines-co/basic-memory/blob/1d0e09b6f4876c3f339c30e8dbfcf66e59e2c356/plugins/codex/schemas)，按 `AGPL-3.0-or-later` 保留；[许可证原文](docs/knowledge/schemas/LICENSE)随文件提供。

项目适配及修改日期见各 schema 文件头部声明。

Basic Memory 运行时独立安装，遵循 `AGPL-3.0-or-later`；仓库未收录其运行时代码，Vue 应用也不打包它。自有接入实现适用 MIT，接入方式见 [项目知识接入](docs/project-knowledge.md)。

## interface-design

`.agents/skills/interface-design/` 来自 [Dammyjay93/interface-design](https://github.com/Dammyjay93/interface-design/tree/2f9be3206855bcb2d1d0af262c8bae25cba6658d/.claude/skills/interface-design)，固定提交 `2f9be3206855bcb2d1d0af262c8bae25cba6658d`，保留上游文件和 [MIT 许可证](.agents/skills/interface-design/LICENSE)。

---
name: mortis-dev
description: >-
  莫提斯的开发流程：对齐需求、产出计划等用户确认、按任务派 implementer 实现、
  verifier 对照计划和测试验收、不通过打回、最后交给用户在浏览器里验收。
  Use when 开始开发、实现功能、修复缺陷、搭骨架、继续未完成的计划，
  or when asked how a development task in Mortis should be carried out.
---

# 莫提斯开发流程

```
对齐需求 → planner 写计划 → 用户确认
→ 每个任务：implementer 实现 → verifier 验收 → 不通过带问题打回 → 通过才提交、进下一个
→ 整体复审 → 浏览器验收 → 向用户报告
```

## 1. 对齐需求

- 先读根目录的《产品思路》《技术方向》《刮削设置》《表结构》和 `AGENTS.md`。文档和对话冲突时以文档为准；要改结论，先改文档再写代码。
- 新功能先用 `brainstorming` 对齐。没定的点用 AskQuestion 问，做成选择题，带推荐项。
- 简单改动（错字、样式、一两行的修复）跳过计划，直接做，做完照第 5 步验证。

## 2. 写计划（planner）

- 用 `writing-plans`。计划存到 `.superpowers/plans/YYYY-MM-DD-<主题>.md`，这个目录不进 git。
- 计划的 Global Constraints 照抄 `AGENTS.md` 和四份文档里与本次相关的硬约束，原文照抄，不转述。
- 一份计划只做一块能单独验收的东西。每个任务要写明：改哪些文件、测试代码、运行命令和预期输出。
- 写完先自查，然后**停下，把计划路径和任务清单给用户确认**。用户没同意不开工。

## 3. 提交

- 稳定版本之前不开分支，直接在 `main` 上做。
- 每个任务验收通过后提交一次。提交说明用中文，格式 `类型: 说明`（`feat`、`fix`、`test`、`chore`、`docs`）。
- 开始执行计划前记下当前提交，作为整体复审的起点。
- **不 push，不改远程。** 要用户明确同意。

## 4. 逐项实现和验收

用 `subagent-driven-development` 执行，进度记在 `.superpowers/sdd/progress.md`。

- **implementer**：每个任务派一个新的子智能体，只给它这个任务的说明、相关接口和 Global Constraints。照 `test-driven-development` 先写失败的测试，再实现。
- **verifier**：另派一个子智能体，拿任务说明、implementer 的报告和这次的改动，给出两个结论：是否符合计划、代码质量是否过关。
- **打回**：严重和重要问题交给修复的子智能体，带上具体问题和要重跑的测试；修完再验收一次。次要问题记进进度文件，留给整体复审处理。
- implementer 卡住时：缺信息就补信息，任务太大就拆小，计划本身有错就停下问用户。
- 全部任务做完后，用最强的模型对本次计划的全部提交（从起点到当前）复审一次。

## 5. 验收

- 声称完成之前，照 `verification-before-completion` 实际运行测试和构建，看到输出再下结论。
- 凡是浏览器里能看到的改动，先自己启动服务、在浏览器里点一遍，再把网址和要打开的页面给用户验收。网址用纯文本给出，不放进代码块。
- 验收要用的数据由我准备好，用户打开页面就能看到效果，不需要自己造数据或改配置。

## 6. 收尾

- 向用户报告：做了什么、测试结果、还有哪些已知问题。是否推送由用户决定。
- 改了启动或打包命令，同步更新 `mortis-serve`、`mortis-release`（存在时）。
- 改了产品或技术结论，同步更新对应文档。不新建别的说明文档。

## 本机环境

- Python：conda 环境 `python314`（`D:\python\envs\python314`）。依赖用这个环境里的 pip 装。
- 前端：Node 22（nvm，在 `D:\nvm`），用 npm。
- .NET 8 SDK：`D:\dev\dotnet`。
- pip、npm、NuGet、conda、PyInstaller 的缓存都在 `D:\dev\cache`，已写进用户环境变量。不要把工具或缓存装到 C 盘。
- 仓库在 `D:\personal开发\mortis`。
- 参考源码：Amane 克隆在 `D:\personal开发\_ref\amane`（GPL-3.0，可改用，保留原版权声明）。只读，不在里面改。

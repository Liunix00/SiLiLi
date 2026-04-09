---
name: run-and-review
description: >-
  Run the Silili agent and review its output against HumanNote originals. Use
  when testing agent changes, debugging prompt output, validating plan/progress
  generation, or when the user says "run agent", "test agent", or "review output".
---

# 运行 Agent 并审阅输出

## 执行流程

### Step 1: 清理旧输出

```bash
rm -rf RobotNote/Projects/ RobotNote/_silili_state/
```

注意：`cmd_run` 已内置启动前清理 `RobotNote/Projects/`，但手动清理 `_silili_state/` 可以让 Agent 以"首次运行"模式读取全部 steps。

### Step 2: 运行 Agent

默认使用限定范围（单项目 + 单人），避免浪费 LLM 调用：

```bash
PYTHONPATH=. python agent/run.py --run --project 001-问津 --person 刘玮康
```

全量运行（所有项目、所有人员）：

```bash
PYTHONPATH=. python agent/run.py --run
```

### Step 3: 审阅输出

运行完成后，逐一检查 `RobotNote/Projects/{project_id}/` 下的文件：

1. **plan.md** — 对照 `HumanNote/Projects/{id}/{id}-plan.md`
   - 格式是否正确（父/子任务行模板）
   - 状态 emoji 是否正确反映 steps 中的标记
   - 是否有非法新增的父任务（没有 `plan` 标记却新建）
   - 任务名称是否被擅自修改

2. **progress.md** — 对照 `HumanNote/Projects/{id}/{id}-progress.md`
   - 是否按日期分组（`### YYYY.M.D`）
   - 日期是否与 steps 中的日期一致（不是用今天日期代替）
   - 是否只记录了实际发生的事实（无 todo/plan/idea/blocked by 内容）
   - 任务名称是否与 plan 一致

3. **idea.md** — 对照 `HumanNote/Projects/{id}/{id}-idea.md`
   - 新 idea 是否正确追加
   - 日期是否与 steps 中出现 idea 标记的日期一致

4. **plan_diff.md / progress_diff.md** — 快速浏览变更是否合理

### Step 4: 报告问题

汇总发现的问题，按严重程度分类：
- **格式错误**：违反 Plan/Progress 格式规范
- **内容错误**：跨项目污染、编造内容、遗漏实际进展
- **逻辑错误**：标记语义处理不正确（如 todo 写入了 progress）

## 常见问题排查

| 现象 | 可能原因 | 排查方向 |
|------|---------|---------|
| progress 日期全是今天 | steps 提取丢失日期标题 | 检查 `_extract_project_steps` |
| 出现其他项目的内容 | 项目标签匹配太宽松 | 检查 `tag_re` 正则 |
| plan 多出莫名任务 | LLM 幻觉 | 检查 `plan_update.j2` 约束是否足够 |
| idea 未被提取 | `_extract_ideas` 正则不匹配 | 检查 idea 行格式 |

# 求解引擎

- `calendar.py`：自然时间 ↔ 可生产分钟，班次日历。
- `model.py`：`Problem / Task / WorkCenter / Operator / FixedAssignment` 领域对象。
- `cpsat_solver.py`：OR-Tools CP-SAT，设备-人员可选区间、NoOverlap、拖期目标。
- `heuristic.py`：列表调度，用于预演与兜底。
- `metrics.py`：交期、拖期、利用率、瓶颈。

该目录不依赖数据库与 Web 层，可单独单测。

"""排产问题的领域模型（与 Web 层、数据库解耦，便于单元测试与替换求解器）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkCenter:
    """设备（工作中心）。"""

    id: int
    code: str
    name: str
    machine_type: str
    precision_grade: int
    blocks: list[tuple[int, int]] = field(default_factory=list)  # 不可用区间（分钟）
    efficiency: float = 1.0


@dataclass
class Operator:
    """操作人员。"""

    id: int
    code: str
    name: str
    level: int
    skills: dict[str, int] = field(default_factory=dict)  # 工序类型 -> 技能等级
    blocks: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Task:
    """一道工序（订单 × 工艺路线工序）。"""

    key: str
    order_id: int
    order_code: str
    product_code: str
    product_family: str
    seq: int
    name: str
    process_type: str
    duration: int                       # 分钟（含批量加工时间与一次装夹准备）
    quantity: int
    required_level: int
    min_start: int = 0                  # 可开工时间（物料齐套）
    due: int = 0                        # 订单交期（分钟坐标）
    priority: int = 2
    weight: int = 10
    eligible_machines: list[int] = field(default_factory=list)
    eligible_operators: list[int] = field(default_factory=list)
    setup_minutes: int = 0

    @property
    def pairs(self) -> list[tuple[int, int]]:
        return [(m, w) for m in self.eligible_machines for w in self.eligible_operators]


@dataclass
class FixedAssignment:
    """冻结的工序（已开工 / 已完工），重排时必须原样保留。"""

    key: str
    machine_id: int
    operator_id: int
    start: int
    duration: int

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class Problem:
    tasks: list[Task]
    machines: dict[int, WorkCenter]
    operators: dict[int, Operator]
    horizon: int
    fixed: list[FixedAssignment] = field(default_factory=list)
    windows: list[tuple[int, int]] = field(default_factory=list)  # 可开工时段（每班次连续区间）
    warnings: list[str] = field(default_factory=list)
    now: int = 0

    def machine(self, mid: int) -> WorkCenter:
        return self.machines[mid]

    def operator(self, oid: int) -> Operator:
        return self.operators[oid]

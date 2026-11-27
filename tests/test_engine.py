"""排产引擎单元测试：产能互斥、技能准入、工序先后、冻结保持。"""

from app.engine.cpsat_solver import solve_cpsat
from app.engine.heuristic import solve_heuristic
from app.engine.model import FixedAssignment, Operator, Problem, Task, WorkCenter


def build_problem(fixed: list[FixedAssignment] | None = None) -> Problem:
    machines = {
        1: WorkCenter(id=1, code="M1", name="五轴", machine_type="mill", precision_grade=3),
        2: WorkCenter(id=2, code="M2", name="三轴", machine_type="mill", precision_grade=2),
    }
    operators = {
        1: Operator(id=1, code="W1", name="王强", level=4, skills={"mill": 4}),
        2: Operator(id=2, code="W2", name="李敏", level=2, skills={"mill": 2}),
    }
    tasks = [
        Task(key="A-1", order_id=1, order_code="A", product_code="P1", product_family="A", seq=1,
             name="粗铣", process_type="mill", duration=120, quantity=10, required_level=3,
             min_start=0, due=600, priority=3, weight=40,
             eligible_machines=[1, 2], eligible_operators=[1]),
        Task(key="A-2", order_id=1, order_code="A", product_code="P1", product_family="A", seq=2,
             name="精铣", process_type="mill", duration=90, quantity=10, required_level=3,
             min_start=0, due=600, priority=3, weight=40,
             eligible_machines=[1], eligible_operators=[1]),
        Task(key="B-1", order_id=2, order_code="B", product_code="P2", product_family="B", seq=1,
             name="粗铣", process_type="mill", duration=80, quantity=8, required_level=1,
             min_start=0, due=1200, priority=1, weight=5,
             eligible_machines=[1, 2], eligible_operators=[1, 2]),
    ]
    return Problem(tasks=tasks, machines=machines, operators=operators, horizon=4000, fixed=fixed or [])


def check_assignment(problem: Problem, assignments: dict) -> None:
    for key, a in assignments.items():
        assert a["end"] - a["start"] > 0
    for resource in ("machine_id", "operator_id"):
        by_resource: dict[int, list] = {}
        for a in assignments.values():
            by_resource.setdefault(a[resource], []).append(a)
        for items in by_resource.values():
            items.sort(key=lambda x: x["start"])
            for prev, cur in zip(items, items[1:]):
                assert cur["start"] >= prev["end"], f"{resource} 重叠"
    assert assignments["A-2"]["start"] >= assignments["A-1"]["end"]
    assert assignments["A-1"]["operator_id"] == 1
    assert assignments["A-2"]["machine_id"] == 1


def test_heuristic_respects_constraints():
    problem = build_problem()
    result = solve_heuristic(problem)
    check_assignment(problem, result["assignments"])


def test_cpsat_respects_constraints():
    problem = build_problem()
    result = solve_cpsat(problem, time_limit=3)
    assert result is not None
    assert result["assignments"], result["status"]
    check_assignment(problem, result["assignments"])


def test_frozen_task_kept_and_blocks_machine():
    fixed = [FixedAssignment(key="A-1", machine_id=1, operator_id=1, start=0, duration=120)]
    problem = build_problem(fixed=fixed)
    result = solve_heuristic(problem)
    assert result["assignments"]["A-1"]["start"] == 0
    assert result["assignments"]["A-2"]["start"] >= 120
    assert result["assignments"]["A-2"]["machine_id"] == 1

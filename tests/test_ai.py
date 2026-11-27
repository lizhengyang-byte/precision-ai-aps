"""AI 层单元测试：指令解析与策略推荐。"""

from app.ai.nlu import parse_instruction
from app.ai.strategy import recommend_strategy
from app.engine.model import Operator, Problem, Task, WorkCenter


def test_parse_breakdown():
    parsed = parse_instruction("CNC-M03 故障停机 4 小时，重新排产")
    assert parsed["intent"] == "breakdown"
    assert "CNC-M03" in parsed["entities"]["machine_codes"]
    assert parsed["entities"]["duration_minutes"] == 240


def test_parse_urgent_with_date():
    parsed = parse_instruction("PO20261127001 加急，交期提前到明天")
    assert parsed["intent"] == "urgent"
    assert parsed["entities"]["order_codes"] == ["PO20261127001"]
    assert parsed["entities"]["deadline"]
    assert parsed["entities"]["priority"] == 3


def test_parse_leave_and_quantity():
    parsed = parse_instruction("张伟请假 8 小时")
    assert parsed["intent"] == "leave"
    assert parsed["entities"]["worker_names"] == ["张伟"]
    parsed = parse_instruction("PO20261127003 数量改为 160")
    assert parsed["intent"] == "quantity"
    assert parsed["entities"]["quantity"] == 160


def test_strategy_shapes():
    machines = {1: WorkCenter(id=1, code="M1", name="磨床", machine_type="grind", precision_grade=3)}
    operators = {1: Operator(id=1, code="W1", name="张伟", level=4, skills={"grind": 4})}
    tasks = []
    for i in range(3):
        tasks.append(
            Task(key=f"O{i}-1", order_id=i, order_code=f"O{i}", product_code="P", product_family=str(i),
                 seq=1, name="磨", process_type="grind", duration=200, quantity=50, required_level=3,
                 min_start=0, due=300 + i * 10, priority=3, weight=40,
                 eligible_machines=[1], eligible_operators=[1])
        )
    problem = Problem(tasks=tasks, machines=machines, operators=operators, horizon=1200)
    advice = recommend_strategy(problem)
    assert advice["strategy"] == "due_date"
    assert advice["weights"]["tardiness"] >= 200

"""命令行演示..."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/demo.db")
os.environ["AUTO_SCHEDULE_ON_START"] = "0"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import models, services  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.seed import reset  # noqa: E402


def print_gantt(assignments, machines, max_rows: int = 12) -> None:
    hours = []
    for a in assignments:
        start = datetime.fromisoformat(a["start"])
        end = datetime.fromisoformat(a["end"])
        hours.append((start, end))
    if not hours:
        print("没有排产结果")
        return
    t0 = min(h[0] for h in hours)
    t1 = max(h[1] for h in hours)
    span = (t1 - t0).total_seconds() / 3600
    width = 88
    print(f"\n甘特图（{t0:%m-%d %H:%M} 起，自然日跨度 {span:.1f} 小时）")
    for m in machines:
        row = [" "] * width
        for a in assignments:
            if a["machine_code"] != m["code"]:
                continue
            s = (datetime.fromisoformat(a["start"]) - t0).total_seconds() / 3600
            e = (datetime.fromisoformat(a["end"]) - t0).total_seconds() / 3600
            start_i = int(s / span * (width - 1))
            end_i = max(start_i + 1, int(e / span * (width - 1)))
            for i in range(start_i, end_i):
                if 0 <= i < width:
                    row[i] = "█"
        print(f"{m['code']:<10}|" + "".join(row))


def plot_gantt(assignments, machines, out: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except Exception:
        print("未安装 matplotlib，跳过图片导出（pip install matplotlib）")
        return False

    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "none"

    t0 = min(datetime.fromisoformat(a["start"]) for a in assignments)
    t1 = max(datetime.fromisoformat(a["end"]) for a in assignments)
    colors = {3: "#ef6b6b", 2: "#f0b429", 1: "#4f8cff"}

    fig, ax = plt.subplots(figsize=(13, 6.2), dpi=150)
    codes = [m["code"] for m in machines]
    for idx, code in enumerate(codes):
        for a in assignments:
            if a["machine_code"] != code:
                continue
            start = datetime.fromisoformat(a["start"])
            end = datetime.fromisoformat(a["end"])
            ax.barh(idx, (end - start).total_seconds() / 3600, left=(start - t0).total_seconds() / 3600,
                     height=0.55, color=colors.get(a.get("priority", 1), "#4f8cff"), edgecolor="white", linewidth=0.5)
            if (end - start).total_seconds() / 3600 > 2.2:
                ax.text((start - t0).total_seconds() / 3600 + 0.1, idx, a["order_code"][-5:],
                        va="center", fontsize=7, color="#0a1120")
    ax.set_yticks(range(len(codes)))
    ax.set_yticklabels(codes, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("小时（自计划起点）", fontsize=9)
    ax.set_title("精密加工车间 · AI 排产甘特图（按设备）", fontsize=13, pad=12)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    ax.legend(handles=[Patch(color=colors[3], label="紧急订单"), Patch(color=colors[2], label="较高优先级"),
                       Patch(color=colors[1], label="普通订单")], loc="upper right", fontsize=8, frameon=False)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".svg"), bbox_inches="tight")
    print(f"甘特图已保存：{out} / {out.with_suffix('.svg')}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true", help="导出甘特图 PNG")
    parser.add_argument("--time-limit", type=float, default=10.0)
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    reset(db)
    version = services.run_schedule(db, strategy="auto", time_limit=args.time_limit, note="命令行演示")
    k = version["metrics"]["kpis"]
    print(f"版本 #{version['id']} | 策略 {version['strategy']} | 求解器 {version['solver']} | 用时 {version['solve_seconds']}s")
    print(f"订单 {k['orders_total']} 张，按期 {k['orders_on_time']} 张，拖期 {k['orders_late']} 张，按期率 {k['on_time_rate']:.0%}")
    print(f"完工跨度 {k['makespan_hours']} 工时（自然日 {k['makespan_calendar_hours']} 小时），瓶颈 {k['bottleneck']['code']} 利用率 {k['bottleneck']['utilization']:.0%}")
    for w in version["metrics"].get("warnings", [])[:5]:
        print("警告：", w)
    machines = [{"code": m.code} for m in db.query(models.Machine).all()]
    print_gantt(version["assignments"], machines)
    if args.plot:
        plot_gantt(version["assignments"], machines, ROOT / "docs" / "images" / "gantt-demo.png")


if __name__ == "__main__":
    main()

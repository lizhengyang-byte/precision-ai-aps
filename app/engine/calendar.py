"""工厂日历：把自然时间映射为“可生产工时”坐标。

精密加工车间通常为两班制（08:00-20:00），周日休息。
排产引擎全部在“可生产分钟”坐标下计算，输出时再换算回自然时间，
这样可以避免夜间、休息日造成的空洞影响约束建模。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable


class ShopCalendar:
    """连续工作日历：内部把每个工作日切成若干连续工作段。"""

    def __init__(
        self,
        start: datetime,
        days: int = 45,
        workday_start: int = 8 * 60,
        workday_end: int = 20 * 60,
        lunch: tuple[int, int] | None = None,
        workdays: Iterable[int] = (0, 1, 2, 3, 4, 5),
    ) -> None:
        self.start = start.replace(second=0, microsecond=0)
        self.workdays = set(workdays)
        self.segments: list[tuple[int, int, datetime, datetime]] = []
        total = 0
        base: date = self.start.date()
        for i in range(days):
            d = base + timedelta(days=i)
            if d.weekday() not in self.workdays:
                continue
            spans = [(workday_start, workday_end)] if not lunch else [(workday_start, lunch[0]), (lunch[1], workday_end)]
            for a, b in spans:
                wall_start = datetime.combine(d, time()) + timedelta(minutes=a)
                wall_end = datetime.combine(d, time()) + timedelta(minutes=b)
                if wall_end <= self.start:
                    continue
                if wall_start < self.start:
                    wall_start = self.start
                minutes = int((wall_end - wall_start).total_seconds() // 60)
                if minutes <= 0:
                    continue
                self.segments.append((total, total + minutes, wall_start, wall_end))
                total += minutes
        self.total_minutes = total

    # ------------------------------------------------------------------ 坐标转换
    def to_wm(self, moment: datetime) -> int:
        """自然时间 -> 可生产分钟（落在非工作时间为该段结束点）。"""
        if moment <= self.start:
            return 0
        if not self.segments:
            return 0
        for start_wm, end_wm, wall_start, wall_end in self.segments:
            if wall_start <= moment < wall_end:
                return start_wm + int((moment - wall_start).total_seconds() // 60)
            if moment < wall_start:
                return start_wm
        last_end_wm, _, _, last_wall_end = self.segments[-1]
        return last_end_wm + int((moment - last_wall_end).total_seconds() // 60)

    def to_datetime(self, wm: float) -> datetime:
        """可生产分钟 -> 自然时间。"""
        if not self.segments:
            return self.start
        wm = max(0, int(wm))
        for start_wm, end_wm, wall_start, wall_end in self.segments:
            if wm < end_wm:
                return wall_start + timedelta(minutes=wm - start_wm)
        last_end_wm, _, _, last_wall_end = self.segments[-1]
        return last_wall_end + timedelta(minutes=wm - last_end_wm)

    def block(self, begin: datetime, finish: datetime) -> tuple[int, int]:
        """把自然时间区间转成不可用区间（用于设备故障、请假等）。"""
        return self.to_wm(begin), max(self.to_wm(begin) + 1, self.to_wm(finish))

    def available_minutes(self, work_centers: int = 1) -> int:
        return self.total_minutes * work_centers

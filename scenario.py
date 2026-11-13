"""验收场景：合成地区维表（R=地区，G=大区）+ 8 次回溯修订。

约定：
- system 是获知时间（修订到达系统的时刻），from/to/at 是业务时间。
- 全部 8 次修订都是回溯性的：修订的业务区间都早于其 system 到达时刻。
- 同 system 冲突放在第 3 次修订里（d-r3-a 与 d-r3-b 同刻度，字典序后者胜出）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ------------------------------------------------------------ 初始装载
INITIAL_DIMS = [
    {"type": "dimension", "entity": "R", "from": 0, "to": 20, "system": 0,
     "version": "d-r1-0", "op": "upsert", "label": "A"},
    {"type": "dimension", "entity": "R", "from": 20, "to": 40, "system": 0,
     "version": "d-r2-0", "op": "upsert", "label": "B"},
    {"type": "dimension", "entity": "R", "from": 40, "to": 60, "system": 0,
     "version": "d-r3-0", "op": "upsert", "label": "A"},
    {"type": "dimension", "entity": "G", "from": 0, "to": 60, "system": 0,
     "version": "d-g1-0", "op": "upsert", "label": "C"},
]

INITIAL_FACTS = [
    {"type": "fact", "id": "F1", "entity": "R", "at": 5, "system": 1,
     "version": "f1"},
    {"type": "fact", "id": "F2", "entity": "R", "at": 15, "system": 1,
     "version": "f2"},
    {"type": "fact", "id": "F3", "entity": "R", "at": 25, "system": 1,
     "version": "f3"},
    {"type": "fact", "id": "F4", "entity": "R", "at": 10, "system": 1,
     "version": "f4"},
    {"type": "fact", "id": "F5", "entity": "R", "at": 35, "system": 1,
     "version": "f5"},
    {"type": "fact", "id": "F6", "entity": "R", "at": 45, "system": 1,
     "version": "f6"},
    {"type": "fact", "id": "F7", "entity": "R", "at": 70, "system": 1,
     "version": "f7"},   # 业务时间落在维表缺口里 -> no_dimension
    {"type": "fact", "id": "F8", "entity": "G", "at": 30, "system": 1,
     "version": "f8"},
]

INITIAL_BATCH = INITIAL_DIMS + INITIAL_FACTS


# ------------------------------------------------------------ 8 次回溯修订
# 每条记录：名称、system 刻度、事件、手工计算的实际重关联键集合
REVISIONS: List[dict] = [
    {
        "name": "Rv1 拆分修正 R[10,30)=X",
        "system": 10,
        "events": [
            {"type": "dimension", "entity": "R", "from": 10, "to": 30,
             "system": 10, "version": "d-r1", "op": "upsert", "label": "X"},
        ],
        "expected_affected": ["F2", "F3", "F4"],  # F4.at=10 被半开区间包含
    },
    {
        "name": "Rv2 回收遮蔽 R[40,60)",
        "system": 20,
        "events": [
            {"type": "dimension", "entity": "R", "from": 40, "to": 60,
             "system": 20, "version": "d-r3-remove", "op": "remove"},
        ],
        "expected_affected": ["F6"],
    },
    {
        "name": "Rv3 同系统时间冲突，字典序大的 d-r3-b 胜出 R[15,25)=Y",
        "system": 30,
        "events": [
            {"type": "dimension", "entity": "R", "from": 15, "to": 25,
             "system": 30, "version": "d-r3-a", "op": "upsert", "label": "OLD"},
            {"type": "dimension", "entity": "R", "from": 15, "to": 25,
             "system": 30, "version": "d-r3-b", "op": "upsert", "label": "Y"},
        ],
        "expected_affected": ["F2"],   # F3.at=25 被半开区间排除
    },
    {
        "name": "Rv4 补录事实 F9（R,at=50，落入已遮蔽区间）",
        "system": 40,
        "events": [
            {"type": "fact", "id": "F9", "entity": "R", "at": 50,
             "system": 40, "version": "f9"},
        ],
        "expected_affected": ["F9"],
    },
    {
        "name": "Rv5 撤销事实 F2",
        "system": 50,
        "events": [
            {"type": "fact", "id": "F2", "entity": "R", "at": 15,
             "system": 50, "version": "f2-del", "op": "delete"},
        ],
        "expected_affected": ["F2"],
    },
    {
        "name": "Rv6 撤销回收 R[40,60)=A-restored",
        "system": 60,
        "events": [
            {"type": "dimension", "entity": "R", "from": 40, "to": 60,
             "system": 60, "version": "d-r6", "op": "upsert",
             "label": "A-restored"},
        ],
        "expected_affected": ["F6", "F9"],
    },
    {
        "name": "Rv7 事实 F1 修订业务时刻 5->6",
        "system": 70,
        "events": [
            {"type": "fact", "id": "F1", "entity": "R", "at": 6,
             "system": 70, "version": "f1-fix"},
        ],
        "expected_affected": ["F1"],
    },
    {
        "name": "Rv8 跨分区修订 G[20,40)=D",
        "system": 80,
        "partition": "p2",
        "events": [
            {"type": "dimension", "entity": "G", "from": 20, "to": 40,
             "system": 80, "version": "d-g8", "op": "upsert", "label": "D",
             "partition": "p2"},
        ],
        "expected_affected": ["F8"],
    },
]

ALL_REVISION_EVENTS: List[dict] = [e for r in REVISIONS for e in r["events"]]

# ------------------------------------------------------------ 12 个已知系统时间切点
# 每个切点右侧给出该时刻报表中每条事实的 (dimension_version, reason)；
# 被删除/尚不存在的事实不出现在字典里。
_CUTS: List[Tuple[int, Dict[str, Tuple[str, str]]]] = [
    (0, {}),
    (1, {
        "F1": ("d-r1-0", "matched"), "F2": ("d-r1-0", "matched"),
        "F3": ("d-r2-0", "matched"), "F4": ("d-r1-0", "matched"),
        "F5": ("d-r2-0", "matched"), "F6": ("d-r3-0", "matched"),
        "F7": (None, "no_dimension"), "F8": ("d-g1-0", "matched"),
    }),
    (9, None), (10, None), (19, None), (20, None), (29, None),
    (30, None), (39, None), (40, None), (41, None), (100, None),
]

# 相邻系统区间边界：左右两侧各查一次
ADJACENT_SYSTEM_POINTS = [(9, 10), (19, 20), (29, 30), (39, 40)]

# 业务时间半开边界（asof=100 时对 R 逐点探测）：(左点, 右点, 左点版本, 右点版本)
VALID_BOUNDARIES = [
    (9, 10, "d-r1-0", "d-r1"),     # Rv1 [10,30)
    (29, 30, "d-r1", "d-r2-0"),
    (14, 15, "d-r1", "d-r3-b"),    # Rv3 [15,25)
    (24, 25, "d-r3-b", "d-r1"),
    (39, 40, "d-r2-0", "d-r6"),    # Rv6 恢复后的 [40,60)
    (59, 60, "d-r6", None),        # 60 起 R 无维表
]


def _state_at_10() -> Dict[str, Tuple[str, str]]:
    return {
        "F1": ("d-r1-0", "matched"), "F2": ("d-r1", "matched"),
        "F3": ("d-r1", "matched"), "F4": ("d-r1", "matched"),
        "F5": ("d-r2-0", "matched"), "F6": ("d-r3-0", "matched"),
        "F7": (None, "no_dimension"), "F8": ("d-g1-0", "matched"),
    }


def _state_at_20() -> Dict[str, Tuple[str, str]]:
    s = _state_at_10()
    s["F6"] = (None, "removed")
    return s


def _state_at_30() -> Dict[str, Tuple[str, str]]:
    s = _state_at_20()
    s["F2"] = ("d-r3-b", "matched")
    return s


def _state_at_40() -> Dict[str, Tuple[str, str]]:
    s = _state_at_30()
    s["F9"] = (None, "removed")
    return s


def _state_at_100() -> Dict[str, Tuple[str, str]]:
    return {
        "F1": ("d-r1-0", "matched"),
        # F2 已删除
        "F3": ("d-r1", "matched"),
        "F4": ("d-r1", "matched"),   # at=10 永久落在 Rv1 [10,30)
        "F5": ("d-r2-0", "matched"),
        "F6": ("d-r6", "matched"),
        "F7": (None, "no_dimension"),
        "F8": ("d-g8", "matched"),
        "F9": ("d-r6", "matched"),
    }


_EXPLICIT = {
    0: {},
    1: _CUTS[1][1],
    10: _state_at_10(),
    20: _state_at_20(),
    30: _state_at_30(),
    40: _state_at_40(),
    100: _state_at_100(),
}


def expected_state(cut: int) -> Dict[str, Tuple[str, str]]:
    """手工预言机：返回 cut 时刻每条事实的 (dimension_version, reason)。"""
    if cut in _EXPLICIT:
        return dict(_EXPLICIT[cut])
    # 切点落在两个修订刻度之间时，状态等于最近一次已生效修订后的状态
    anchor = max(t for t in _EXPLICIT if t <= cut)
    return dict(_EXPLICIT[anchor])


CUTS: List[int] = [c[0] for c in _CUTS]

# 回放批次大小：同一组版本用不同切批方式重放
REPLAY_BATCH_SIZES = [1, 2, 3, 5]


"""ctypes bindings for libfccore (see include/fccore.h)."""

import ctypes as C
import os
from dataclasses import dataclass
from pathlib import Path

_LIB_PATH = Path(
    os.environ.get(
        "FCCORE_LIB", Path(__file__).resolve().parent.parent / "build" / "libfccore.so"
    )
)
_lib = C.CDLL(str(_LIB_PATH))

API_VERSION = 1

# enum fc_block_type
STATIC_RECT, STATIC_CIRCLE, DYNAMIC_RECT, DYNAMIC_CIRCLE = 0, 1, 2, 3
GOAL_RECT, GOAL_CIRCLE, CW_GOAL_CIRCLE, CCW_GOAL_CIRCLE = 4, 5, 6, 7
WHEEL, CW_WHEEL, CCW_WHEEL, WATER_ROD, SOLID_ROD = 8, 9, 10, 11, 12

SHAPE_RECT, SHAPE_CIRCLE = 0, 1

# enum fc_win_check
WIN_FLASH, WIN_FCSIM = 0, 1

# fc_sim_new_opts flags
OPT_FCSIM_IMPORT = 1 << 0
OPT_FCSIM_WIN_CHECK = 1 << 1
OPT_FCSIM = OPT_FCSIM_IMPORT | OPT_FCSIM_WIN_CHECK

ILLEGAL_OVERLAP = 1 << 0
ILLEGAL_OUTSIDE_BUILD_AREA = 1 << 1


class BlockInfo(C.Structure):
    _fields_ = [
        ("type_id", C.c_int32),
        ("shape", C.c_int32),
        ("is_level", C.c_int32),
        ("is_goal", C.c_int32),
        ("xml_id", C.c_int32),
        ("w", C.c_double),
        ("h", C.c_double),
        ("radius", C.c_double),
    ]


class BodyState(C.Structure):
    _fields_ = [(n, C.c_double) for n in ("x", "y", "angle", "vx", "vy", "omega")]


class JointInfo(C.Structure):
    _fields_ = [
        ("block1", C.c_int32),
        ("block2", C.c_int32),
        ("x", C.c_double),
        ("y", C.c_double),
    ]


class DesignJointInfo(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double),
                ("gen", C.c_int32), ("n_att", C.c_int32)]


@dataclass(frozen=True)
class DesignJoint:
    """A joint of the design (a joint stack): position, generating block
    (or -1) and attached blocks in attach order."""
    x: float
    y: float
    gen: int
    att: tuple


class Area(C.Structure):
    _fields_ = [(n, C.c_double) for n in ("x", "y", "w", "h")]


_P = C.c_void_p


def _sig(name, res, *args):
    f = getattr(_lib, name)
    f.restype = res
    f.argtypes = list(args)


_sig("fc_sim_new_opts", _P, C.c_char_p, C.c_int32, C.c_uint32)
_sig("fc_sim_free", None, _P)
_sig("fc_sim_reset", None, _P)
_sig("fc_sim_set_win_check", None, _P, C.c_int32)
_sig("fc_sim_block_count", C.c_int32, _P)
_sig("fc_sim_design_block_count", C.c_int32, _P)
_sig("fc_sim_get_blocks", C.c_int32, _P, C.POINTER(BlockInfo), C.c_int32)
_sig("fc_sim_get_state", C.c_int32, _P, C.POINTER(BodyState), C.c_int32)
_sig("fc_sim_get_joints", C.c_int32, _P, C.POINTER(JointInfo), C.c_int32)
_sig("fc_sim_design_joint_count", C.c_int32, _P)
_sig("fc_sim_get_design_joint", C.c_int32, _P, C.c_int32, C.POINTER(DesignJointInfo),
     C.POINTER(C.c_int32), C.c_int32)
_sig("fc_sim_get_block_joint_ids", C.c_int32, _P, C.c_int32, C.POINTER(C.c_int32),
     C.c_int32)
_sig("fc_sim_get_build_area", None, _P, C.POINTER(Area))
_sig("fc_sim_get_goal_area", None, _P, C.POINTER(Area))
_sig("fc_sim_step", None, _P, C.c_int64)
_sig("fc_sim_run", C.c_int64, _P, C.c_int64)
_sig("fc_sim_record", C.c_int64, _P, C.c_int64, C.c_int64, C.POINTER(C.c_int32),
     C.c_int32, C.POINTER(BodyState))
_sig("fc_sim_tick", C.c_int64, _P)
_sig("fc_sim_solve_tick", C.c_int64, _P)
_sig("fc_sim_goal_reached", C.c_int32, _P)
_sig("fc_sim_check_legal", C.c_int32, _P, C.POINTER(C.c_int32))
_sig("fc_sim_overlaps", C.c_int32, _P, C.POINTER(C.c_int32), C.c_int32)
_sig("fc_sim_overflow", C.c_int32, _P)
_sig("fc_sim_export_xml", C.c_void_p, _P, C.c_char_p, C.c_char_p, C.c_char_p)
_sig("fc_free", None, _P)


@dataclass(frozen=True)
class Legality:
    legal: bool
    flags: list  # per design block, ILLEGAL_* bits


class Sim:
    """One design instantiated as a Box2D world."""

    def __init__(self, xml, win_check=None, opts=0):
        """opts: OPT_* flags (default: follow the original game). win_check,
        if given, overrides the win check selected by opts."""
        if isinstance(xml, str):
            xml = xml.encode()
        self._h = _lib.fc_sim_new_opts(xml, len(xml), opts)
        if not self._h:
            raise ValueError("failed to parse design XML")
        if win_check is not None:
            _lib.fc_sim_set_win_check(self._h, win_check)
        self.n_blocks = _lib.fc_sim_block_count(self._h)
        self.n_design_blocks = _lib.fc_sim_design_block_count(self._h)

    def close(self):
        if self._h:
            _lib.fc_sim_free(self._h)
            self._h = None

    __del__ = close

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def reset(self):
        _lib.fc_sim_reset(self._h)

    def blocks(self):
        arr = (BlockInfo * max(self.n_blocks, 1))()
        _lib.fc_sim_get_blocks(self._h, arr, self.n_blocks)
        return list(arr[: self.n_blocks])

    def state(self):
        arr = (BodyState * max(self.n_blocks, 1))()
        _lib.fc_sim_get_state(self._h, arr, self.n_blocks)
        return list(arr[: self.n_blocks])

    def joints(self):
        n = _lib.fc_sim_get_joints(self._h, None, 0)
        arr = (JointInfo * max(n, 1))()
        _lib.fc_sim_get_joints(self._h, arr, n)
        return list(arr[:n])

    def design_joints(self):
        """All design joints, in the design's joint list order."""
        out = []
        for i in range(_lib.fc_sim_design_joint_count(self._h)):
            info = DesignJointInfo()
            n = _lib.fc_sim_get_design_joint(self._h, i, C.byref(info), None, 0)
            att = (C.c_int32 * max(n, 1))()
            _lib.fc_sim_get_design_joint(self._h, i, C.byref(info), att, n)
            out.append(DesignJoint(info.x, info.y, info.gen, tuple(att[:n])))
        return out

    def block_joint_ids(self, block):
        """Design joint ids of the block's joint slots, in canonical order."""
        ids = (C.c_int32 * 9)()
        n = _lib.fc_sim_get_block_joint_ids(self._h, block, ids, 9)
        if n < 0:
            raise IndexError(block)
        return list(ids[:n])

    def build_area(self):
        a = Area()
        _lib.fc_sim_get_build_area(self._h, C.byref(a))
        return a

    def goal_area(self):
        a = Area()
        _lib.fc_sim_get_goal_area(self._h, C.byref(a))
        return a

    def step(self, n=1):
        _lib.fc_sim_step(self._h, n)

    def record(self, n, stride=1, blocks=None):
        """Step n ticks; return states after every stride-th step as a list of
        frames, each a list of BodyState for `blocks` (default: all)."""
        idx = list(range(self.n_blocks)) if blocks is None else list(blocks)
        frames = n // max(stride, 1)
        out = (BodyState * max(frames * len(idx), 1))()
        arr = (C.c_int32 * max(len(idx), 1))(*idx)
        got = _lib.fc_sim_record(self._h, n, stride, arr, len(idx), out)
        k = len(idx)
        return [list(out[f * k:(f + 1) * k]) for f in range(got)]

    def record_array(self, n, stride=1, blocks=None):
        """Like record(), as a numpy array of shape (frames, blocks, 6) with
        columns x, y, angle, vx, vy, omega."""
        import numpy as np

        idx = list(range(self.n_blocks)) if blocks is None else list(blocks)
        frames = n // max(stride, 1)
        out = np.empty((max(frames, 1), max(len(idx), 1), 6), dtype=np.float64)
        arr = (C.c_int32 * max(len(idx), 1))(*idx)
        got = _lib.fc_sim_record(self._h, n, stride, arr, len(idx),
                                 out.ctypes.data_as(C.POINTER(BodyState)))
        return out[:got, :len(idx)]

    def run(self, max_ticks):
        """Step until solved or max_ticks; returns solve tick or None."""
        t = _lib.fc_sim_run(self._h, max_ticks)
        return None if t < 0 else t

    @property
    def tick(self):
        return _lib.fc_sim_tick(self._h)

    @property
    def solve_tick(self):
        t = _lib.fc_sim_solve_tick(self._h)
        return None if t < 0 else t

    def goal_reached(self):
        return bool(_lib.fc_sim_goal_reached(self._h))

    def check_legal(self):
        flags = (C.c_int32 * max(self.n_design_blocks, 1))()
        legal = _lib.fc_sim_check_legal(self._h, flags)
        return Legality(bool(legal), list(flags[: self.n_design_blocks]))

    def overflow(self):
        """Broad-phase pairs dropped so far because the engine's pair table
        (32768, as in the original game) was full: 0 for any design the
        original can run."""
        return _lib.fc_sim_overflow(self._h)

    def overlaps(self):
        """Pairs (i, j), i < j, of blocks touching at tick 0 (the overlaps
        check_legal flags): indices as in blocks(), design blocks first."""
        n = _lib.fc_sim_overlaps(self._h, None, 0)
        buf = (C.c_int32 * max(2 * n, 1))()
        _lib.fc_sim_overlaps(self._h, buf, n)
        return sorted((buf[2 * k], buf[2 * k + 1]) for k in range(n))

    def export_xml(self, user="", name="", desc=""):
        p = _lib.fc_sim_export_xml(self._h, user.encode(), name.encode(), desc.encode())
        try:
            return C.string_at(p).decode()
        finally:
            _lib.fc_free(p)

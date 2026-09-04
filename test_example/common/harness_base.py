from __future__ import annotations

import ast
import inspect
import json
import math
import os
import subprocess
import sys
from pathlib import Path


def _numeric(value):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clamp01(value):
    f = _numeric(value)
    return 0.0 if f is None else max(0.0, min(1.0, f))


def score_identity(value):
    return clamp01(value)


def score_error(err, perfect, zero):
    f = _numeric(err)
    if f is None:
        return 0.0
    f = abs(f)
    if f <= perfect:
        return 1.0
    if f >= zero:
        return 0.0
    return 1.0 - (f - perfect) / (zero - perfect)


def score_ratio(value, full, zero=0.0):
    f = _numeric(value)
    if f is None:
        return 0.0
    if f >= full:
        return 1.0
    if f <= zero:
        return 0.0
    return (f - zero) / (full - zero)


score_at_least = score_ratio


def score_band(value, lo, hi, zero_err):
    f = _numeric(value)
    if f is None:
        return 0.0
    if lo <= f <= hi:
        return 1.0
    return score_error((lo - f) if f < lo else (f - hi), 0.0, zero_err)


RUN_FLAG = "--_run"


def candidate_from_argv(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) not in (2, 3):
        raise SystemExit("Usage: python3 harness.py candidate.py [timeout_s]")
    return argv[1]


def timeout_from_argv(default, argv=None):
    argv = sys.argv if argv is None else argv
    return float(argv[2]) if len(argv) > 2 else default


def is_run_request(argv=None):
    argv = sys.argv if argv is None else argv
    return len(argv) >= 2 and argv[1] == RUN_FLAG


def safe_check(key, descriptions, check_fn, *args):
    try:
        return check_fn(*args)
    except Exception:
        return (key, False, descriptions.get(key, key))


def all_failed(descriptions, **overrides):
    return {
        key: (key, overrides.get(key, False), desc)
        for key, desc in descriptions.items()
    }


RESULT_VAR_NAMES = ("solid", "result", "model", "robot", "part", "final",
                    "assembly", "body", "output", "res")

MIN_SOLID_VOLUME = 1e-9


def _cq():
    import cadquery as cq
    return cq


def is_shape(obj):
    try:
        cq = _cq()
    except ImportError:
        return False
    return isinstance(obj, (cq.Workplane, cq.Shape, cq.Assembly))


def to_shapes(value):
    cq = _cq()
    shapes = []
    if isinstance(value, cq.Workplane):
        for v in value.vals():
            if isinstance(v, cq.Shape) and v.Volume() > MIN_SOLID_VOLUME:
                shapes.append(v)
    elif isinstance(value, cq.Assembly):
        shapes.append(value.toCompound())
    elif isinstance(value, cq.Shape):
        if value.Volume() > MIN_SOLID_VOLUME:
            shapes.append(value)
    return shapes


def pick_result(namespace, shown=(), preferred_names=RESULT_VAR_NAMES):
    chosen = []
    for obj in shown:
        chosen.extend(to_shapes(obj))
    if chosen:
        return chosen, "show_object"

    for name in preferred_names:
        for key, value in namespace.items():
            if key.lower() == name:
                shapes = to_shapes(value)
                if shapes:
                    return shapes, f"variable:{key}"

    best, best_vol, source = [], -1.0, None
    for key, value in namespace.items():
        if key.startswith("_"):
            continue
        try:
            shapes = to_shapes(value)
        except Exception:
            continue
        if not shapes:
            continue
        bb_vol = 0.0
        for s in shapes:
            bb = s.BoundingBox()
            bb_vol += bb.xlen * bb.ylen * bb.zlen
        if bb_vol > best_vol:
            best, best_vol, source = shapes, bb_vol, f"largest:{key}"
    return best, source


def compound(shapes):
    cq = _cq()
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def safe_volume(shape):
    try:
        return float(shape.Volume())
    except Exception:
        return 0.0


def shape_signature(shape):
    bb = shape.BoundingBox()
    c = shape.Center()
    return {
        "volume": safe_volume(shape),
        "centroid": (c.x, c.y, c.z),
        "bbox": (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax),
    }


def to_mesh(shape, tolerance=0.05, angular_tolerance=0.2, method="stl"):
    import trimesh

    cq = _cq()
    if method == "tessellate":
        from common import geom

        return geom.to_trimesh(shape, tolerance=tolerance)

    import tempfile

    wp = shape if isinstance(shape, cq.Workplane) else cq.Workplane(obj=shape)
    with tempfile.TemporaryDirectory() as d:
        stl = os.path.join(d, "shape.stl")
        cq.exporters.export(wp, stl, tolerance=tolerance,
                            angularTolerance=angular_tolerance)
        return trimesh.load(stl, force="mesh")


def as_shape(value):
    cq = _cq()
    if isinstance(value, cq.Workplane):
        vals = [x for x in value.vals() if isinstance(x, cq.Shape)]
        if not vals:
            return None
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    return value if isinstance(value, cq.Shape) else None


ASSEMBLY_NAME_CANDIDATES = ("solid", "result", "assembly", "part", "model")


def pick_named_shape(namespace, name=None, preferred=ASSEMBLY_NAME_CANDIDATES):
    if name:
        shape = as_shape(namespace.get(name))
        if shape is None:
            raise SystemExit(f"'{name}' is not a shape in that script.")
        return name, shape

    best_name, best_shape, best_score = None, None, -1.0
    for key, value in namespace.items():
        if key.startswith("_"):
            continue
        shape = as_shape(value)
        if shape is None:
            continue
        try:
            bb = shape.BoundingBox()
            envelope = ((bb.xmax - bb.xmin) * (bb.ymax - bb.ymin)
                        * (bb.zmax - bb.zmin))
            if float(shape.Volume()) <= 0:
                continue
        except Exception:
            continue
        score = envelope * (1000.0 if key in preferred else 1.0)
        if score > best_score:
            best_name, best_shape, best_score = key, shape, score
    return best_name, best_shape


VIEWER_MODULES = ("jupyter_cadquery", "ocp_vscode", "cq_editor")


def stub_viewer_modules():
    import types as _types

    for name in VIEWER_MODULES:
        if name not in sys.modules:
            m = _types.ModuleType(name)
            m.show = lambda *a, **k: None
            m.show_object = lambda *a, **k: None
            m.set_port = lambda *a, **k: None
            m.reset_show = lambda *a, **k: None
            sys.modules[name] = m


import contextlib


@contextlib.contextmanager
def scratch_cwd():
    """Run candidate code with its working directory pointed at a throwaway
    temp dir, so relative-path side effects (exports, logs) never land in
    the repo. The previous cwd is always restored."""
    import shutil
    import tempfile

    prev = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="candidate_cwd_")
    os.chdir(tmp)
    try:
        yield tmp
    finally:
        os.chdir(prev)
        shutil.rmtree(tmp, ignore_errors=True)


def exec_script(path, overrides=None, module_name="__main__",
                viewer_stubs=False, script_dir_on_path=False):
    if viewer_stubs:
        stub_viewer_modules()

    path = os.path.abspath(str(path))
    ns = {
        "__name__": module_name,
        "__file__": path,
        "show": lambda *a, **k: None,
        "show_object": lambda *a, **k: None,
        "debug": lambda *a, **k: None,
    }

    script_dir = os.path.dirname(path)
    inserted = False
    if script_dir_on_path and script_dir and script_dir not in sys.path:
        sys.path.insert(0, script_dir)
        inserted = True
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        if overrides:
            apply_overrides(tree, overrides)
            ast.fix_missing_locations(tree)
        with scratch_cwd():
            exec(compile(tree, path, "exec"), ns)
    finally:
        if inserted:
            try:
                sys.path.remove(script_dir)
            except ValueError:
                pass
    return ns


def load_script(path, overrides=None):
    path = Path(path)
    ns = {"__name__": "__main__", "__file__": str(path)}
    shown = []
    ns["show_object"] = lambda obj, *a, **k: shown.append(obj)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if overrides:
            apply_overrides(tree, overrides)
            ast.fix_missing_locations(tree)
        with scratch_cwd():
            exec(compile(tree, str(path), "exec"), ns)
    except Exception as exc:
        ns["__shown__"] = shown
        return ns, exc
    ns["__shown__"] = shown
    return ns, None


def apply_overrides(tree, overrides):
    remaining = dict(overrides)
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for t in targets:
            if t.id in remaining:
                node.value = ast.Constant(value=remaining.pop(t.id))
    if remaining:
        raise RuntimeError(f"Override targets not found: {sorted(remaining)}")


def spawn_run(harness_file, script_path, out_geom, out_meta, timeout,
              overrides=None):
    import time

    cmd = [sys.executable, os.path.abspath(harness_file), RUN_FLAG,
           os.path.abspath(script_path), os.path.abspath(out_geom),
           os.path.abspath(out_meta)]
    if overrides:
        cmd.append(json.dumps(overrides))

    import tempfile

    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="candidate_cwd_") as run_dir:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, cwd=run_dir)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s",
                "elapsed": time.time() - t0}
    elapsed = time.time() - t0

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        return {"ok": False, "error": "\n".join(tail), "elapsed": elapsed}
    if not (os.path.exists(out_geom) and os.path.exists(out_meta)):
        return {"ok": False, "error": "runner produced no output geometry",
                "elapsed": elapsed}

    with open(out_meta, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta.update({"ok": True, "elapsed": elapsed, "geometry": out_geom})
    return meta


def spawn_run_ok(harness_file, script_path, out_geom, out_meta, timeout,
                 overrides=None):
    result = spawn_run(harness_file, script_path, out_geom, out_meta, timeout,
                       overrides=overrides)
    if result.get("ok"):
        return True, None
    return False, result.get("error", "execution failed")


def runner_main(script_path, out_geom, out_meta, overrides_json=None,
                fmt="stl"):
    cq = _cq()

    overrides = json.loads(overrides_json) if overrides_json else None
    ns, err = load_script(script_path, overrides=overrides)
    if err is not None:
        raise err

    chosen, source_of = pick_result(ns, ns.get("__shown__", ()))
    if not chosen:
        raise RuntimeError("Script executed but produced no solid geometry "
                           "(no Workplane/Shape/Assembly with volume found).")

    final = compound(chosen)

    if fmt == "brep":
        final.exportBrep(out_geom)
    else:
        cq.exporters.export(cq.Workplane(obj=final), out_geom,
                            tolerance=0.05, angularTolerance=0.2)

    bb = final.BoundingBox()
    solids = final.Solids()
    meta = {
        "volume": safe_volume(final),
        "bbox": [[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]],
        "n_shapes": len(chosen),
        "n_solids": len(solids),
        "volumes": [safe_volume(s) for s in solids],
        "result_source": source_of,
    }
    with open(out_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f)


TOL = 1e-9


def parse(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)


def names(stmt):
    out = []

    def walk_target(t):
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                walk_target(e)

    if isinstance(stmt, ast.Assign):
        for t in stmt.targets:
            walk_target(t)
    elif isinstance(stmt, ast.AnnAssign):
        walk_target(stmt.target)

    return out


def value_node(stmt):
    if isinstance(stmt, ast.Assign):
        return stmt.value
    if isinstance(stmt, ast.AnnAssign):
        return stmt.value
    return None


def assignments(tree, name):
    return [s for s in tree.body if name in names(s)]


def safe_eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return env[node.id]

    if isinstance(node, ast.UnaryOp):
        v = safe_eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v

    if isinstance(node, ast.BinOp):
        a = safe_eval(node.left, env)
        b = safe_eval(node.right, env)

        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b

    raise ValueError("not statically evaluable")


def env_before(tree, line):
    env = {}

    for stmt in tree.body:
        if getattr(stmt, "lineno", 10**9) >= line:
            break

        ns = names(stmt)
        node = value_node(stmt)

        if len(ns) == 1 and node is not None:
            try:
                env[ns[0]] = safe_eval(node, env)
            except Exception:
                pass

    return env


def value(tree, name, last=True):
    xs = assignments(tree, name)
    if not xs:
        raise KeyError(name)

    stmt = xs[-1] if last else xs[0]
    return safe_eval(value_node(stmt), env_before(tree, stmt.lineno))


def close(a, b):
    return abs(float(a) - float(b)) <= TOL


def methods(node, method):
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == method
    ]


def contains_name(node, name):
    return any(
        isinstance(n, ast.Name) and n.id == name
        for n in ast.walk(node)
    )


def transitively_references(tree, root_name, target_name, _memo=None):
    if _memo is None:
        _memo = set()
    if root_name in _memo:
        return False
    _memo.add(root_name)

    xs = assignments(tree, root_name)
    if not xs:
        return False
    node = value_node(xs[-1])
    if node is None:
        return False
    if contains_name(node, target_name):
        return True

    referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    referenced.discard(root_name)
    return any(transitively_references(tree, r, target_name, _memo) for r in referenced)


def contains_attr(node, attr):
    return any(
        isinstance(n, ast.Attribute) and n.attr == attr
        for n in ast.walk(node)
    )


def normalized_assignment(tree, name):
    xs = assignments(tree, name)
    if not xs:
        return None

    return ast.dump(
        value_node(xs[-1]),
        annotate_fields=True,
        include_attributes=False
    )


def is_numeric_expr(node, known_params=()):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return is_numeric_expr(node.operand, known_params)
    if isinstance(node, ast.BinOp):
        return (is_numeric_expr(node.left, known_params)
                and is_numeric_expr(node.right, known_params))
    if isinstance(node, ast.Name):
        return node.id in known_params
    return False


def referenced_after(tree, name, after_lineno):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id == name
                and isinstance(node.ctx, ast.Load)
                and getattr(node, "lineno", 0) > after_lineno):
            return True
    return False


def scan_imports_and_attrs(tree):
    imports, attrs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            attrs.add(node.func.id)
    return imports, attrs


def load_module(path):
    import runpy

    return runpy.run_path(str(path))


HARNESS_VERSION = "1.0.0"


def _clamp01(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v != v:
        return 0.0
    return min(1.0, max(0.0, v))


def read_task_id(task_dir):
    """Task id from task.toml's [task].name, minus its "storygold/"
    namespace prefix. task.toml is never bundled into a Harbor verifier
    container, so this stays "" there regardless."""
    task_dir = Path(task_dir)
    try:
        import tomllib
        with open(task_dir / "task.toml", "rb") as f:
            cfg = tomllib.load(f)
        name = cfg["task"]["name"]
        return name.split("/", 1)[1] if "/" in name else name
    except Exception:
        return ""


def finalize(task_dir, checks, version=HARNESS_VERSION, must_pass=(),
             weights=None):
    subscores = {}
    for name, entry in checks.items():
        value = entry[1] if isinstance(entry, (tuple, list)) else entry
        subscores[name] = round(_clamp01(value), 4)
    # Gated (must_pass) checks are score-neutral: they appear in subscores
    # but contribute no points. Failing any gate zeroes the total score.
    gated = set(must_pass)
    ungated = {name: v for name, v in subscores.items() if name not in gated}
    # Optional per-check weights ({check_name: weight}, default 1). Subscores
    # stay clamped to [0, 1]; each ungated check contributes subscore * weight
    # and adds its weight to max_score. Weight 0 contributes nothing.
    weights = weights or {}
    score = round(sum(v * weights.get(name, 1)
                      for name, v in ungated.items()), 4)
    failed_must_pass = [name for name in must_pass
                        if subscores.get(name, 1.0) < 1.0]
    if failed_must_pass:
        score = 0.0
    envelope = {
        "task_id": read_task_id(task_dir),
        "score": score,
        "max_score": sum(weights.get(name, 1) for name in ungated),
        "passed": bool(subscores) and all(v >= 1.0 for v in subscores.values()),
        "subscores": subscores,
        "harness_version": version,
    }
    if must_pass:
        envelope["must_pass"] = list(must_pass)
    return envelope


# --------------------------------------------------------------------------
# Harness base class
# --------------------------------------------------------------------------

class Harness:
    """Base class for task harnesses: all the scaffolding, none of the checks.

    A task harness subclasses Harness and provides exactly two things:

        class MyTask(Harness):
            SCORING = {...}                 # declarative scored metrics (optional)
            THRESHOLDS = {...}              # named tunables (optional)

            def build_state(self, candidate_path):   # expensive shared setup
                ...
            def checks(self, state):                 # {name: (name, ok, desc)}
                ...

        main = MyTask.as_main()
        if __name__ == "__main__":
            MyTask.cli()

    Everything else is provided here:

    - CLI contract: candidate path from argv[1], optional grading timeout from
      argv[2] (available as self.timeout inside build_state/checks).
    - The --_run self-reinvocation dispatch used by spawn_run.
    - finalize() envelope + the __main__ JSON printing entry point.
    - SCORING-driven scored checks: kind "identity" (score is the metric itself,
      clamped to 1.0 from `full_at`) and kind "error" (1.0 at <= `perfect`,
      linear to 0.0 at `zero_at`), via self.scored_checks(state).

    SCORING entries: {key: {"metric": <state metrics key or None>,
                            "kind": "identity"|"error",
                            "full_at": float          (identity),
                            "perfect": float, "zero_at": float   (error),
                            "desc": str}}
    Override metric_value() for metrics that need computing rather than lookup.

    Set CANDIDATE_OPTIONAL = True for live-document harnesses (e.g. SolidWorks)
    that grade whatever is open when no candidate path is given; build_state
    then receives None.
    """

    THRESHOLDS = {}
    SCORING = {}
    BUILD_TIMEOUT_S = 600
    RUNNER_FMT = "stl"
    CANDIDATE_OPTIONAL = False
    # Check names whose failure zeroes the total score (see finalize).
    MUST_PASS = ()
    # Optional {check_name: weight} for scored checks (default 1 each; see
    # finalize).
    WEIGHTS = {}

    # ------------------------------------------------------------------
    # per-task hooks

    def build_state(self, candidate_path):
        """Expensive shared setup; returns the state dict checks read."""
        raise NotImplementedError

    def checks(self, state):
        """{check_name: (check_name, ok, description)} -- ok is a bool or
        a float in [0, 1]."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # provided scaffolding

    @classmethod
    def harness_file(cls):
        # Prefer the defining module's __file__: inspect.getfile fails on
        # classes from modules loaded without a sys.modules registration.
        mod = sys.modules.get(cls.__module__)
        path = getattr(mod, "__file__", None) or inspect.getfile(cls)
        return str(Path(path).resolve())

    @classmethod
    def task_dir(cls):
        """Directory holding this task's task.toml. The harness may run
        from its original `<task>/harness/harness.py` location or from the
        Harbor-generated `<task>/tests/task/harness/harness.py` copy, which
        sits two directories deeper -- walk up until task.toml turns up
        (never found inside a Harbor verifier container, where task.toml
        isn't bundled; read_task_id() already tolerates that)."""
        d = Path(cls.harness_file()).parent
        for _ in range(5):
            d = d.parent
            if (d / "task.toml").is_file():
                return d
        return Path(cls.harness_file()).parent.parent

    def main(self):
        if is_run_request():
            runner_main(*sys.argv[2:6], fmt=self.RUNNER_FMT)
            raise SystemExit(0)
        if self.CANDIDATE_OPTIONAL and len(sys.argv) == 1:
            candidate = None
        else:
            candidate = candidate_from_argv()
        self.timeout = timeout_from_argv(self.BUILD_TIMEOUT_S)
        state = self.build_state(candidate)
        return finalize(self.task_dir(), self.checks(state),
                        must_pass=self.MUST_PASS, weights=self.WEIGHTS)

    @classmethod
    def as_main(cls):
        """Module-level main() compatible with tools/report.py."""
        def main():
            return cls().main()
        return main

    @classmethod
    def cli(cls):
        print(json.dumps(cls().main(), indent=1))
        raise SystemExit(0)

    # ------------------------------------------------------------------
    # SCORING-driven scored checks

    def metric_value(self, state, key):
        metrics = state.get("metrics")
        if metrics is None:
            return None
        return metrics.get(self.SCORING[key]["metric"])

    def scored_check(self, state, key):
        cfg = self.SCORING[key]
        value = self.metric_value(state, key)
        if cfg["kind"] == "identity":
            score = score_identity(value)
            if score >= cfg["full_at"]:
                score = 1.0
            return (key, score,
                    f"{cfg['desc']} (score = the metric itself, 1.0 from {cfg['full_at']})")
        return (key, score_error(value, cfg["perfect"], cfg["zero_at"]),
                f"{cfg['desc']}: 1.0 at <= {cfg['perfect']}, 0 at {cfg['zero_at']}")

    def scored_checks(self, state):
        return {key: self.scored_check(state, key) for key in self.SCORING}

from __future__ import annotations

import math
import statistics

try:
    import pythoncom
    from win32com.client import VARIANT
except ImportError:
    pythoncom = None
    VARIANT = None

SW_BODY_INTERSECT = 15901
SW_THIS_CONFIGURATION = 1

LABEL_FACE_MAX_AREA = 15e-6
SAMPLES_PER_EDGE = 9
BBOX_PAD = 1e-4
MIN_INTERFERENCE_VOLUME = 1e-12
_SKEW_EPS = 1e-18


def z(member):
    if not callable(member):
        return member
    try:
        return member()
    except Exception:
        return member


def _edge_param_range(edge):
    try:
        cpd = z(edge.GetCurveParams3)
        return float(cpd.UMinValue), float(cpd.UMaxValue)
    except Exception:
        pass
    p = z(edge.GetCurveParams2)
    return float(p[6]), float(p[7])


def _sample_face_outline(face, samples_per_edge: int = SAMPLES_PER_EDGE):
    pts = []
    edges = z(face.GetEdges)
    if not edges:
        return pts
    n = samples_per_edge - 1
    for edge in edges:
        try:
            curve = z(edge.GetCurve)
            t0, t1 = _edge_param_range(edge)
            for k in range(n + 1):
                t = t0 + (t1 - t0) * k / float(n)
                ev = curve.Evaluate2(t, 0)
                pts.append((float(ev[0]), float(ev[1]), float(ev[2])))
        except Exception:
            continue
    return pts


def _standardised_skew(values, mean: float) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    m2 = sum((v - mean) ** 2 for v in values) / n
    m3 = sum((v - mean) ** 3 for v in values) / n
    return (m3 / (m2 ** 1.5)) if m2 > _SKEW_EPS else 0.0


def face_metrics(face) -> dict:
    try:
        area = float(z(face.GetArea))
    except Exception:
        area = 0.0
    try:
        loops = int(z(face.GetLoopCount))
    except Exception:
        loops = 0
    try:
        box = z(face.GetBox)
    except Exception:
        box = None

    pts = _sample_face_outline(face)
    n = len(pts)
    if n:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        cx, cy, cz = sum(xs) / n, sum(ys) / n, sum(zs) / n
        skew_x = _standardised_skew(xs, cx)
        skew_z = _standardised_skew(zs, cz)
    else:
        cx = cy = cz = skew_x = skew_z = 0.0

    return {
        "area_m2": area,
        "loops": loops,
        "centroid_m": [cx, cy, cz],
        "skew_x": skew_x,
        "skew_z": skew_z,
        "outline_samples": n,
        "_box_xmin": (float(box[0]) if box else 0.0),
        "_box_zmin": (float(box[2]) if box else 0.0),
    }


def _appearance_key(area_m2: float, box_xmin_m: float, box_zmin_m: float) -> str:
    return f"{area_m2 * 1e6:.4f}|{box_xmin_m * 1000:.2f},{box_zmin_m * 1000:.2f}"


def build_appearance_map(model_doc):
    out = {}
    try:
        materials = model_doc.Extension.GetRenderMaterials2(SW_THIS_CONFIGURATION, None)
    except Exception:
        return out
    if not materials:
        return out

    for rm in materials:
        name, rgb = "", None
        try:
            fn = str(rm.FileName)
            name = fn.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
        except Exception:
            pass
        try:
            colorref = int(rm.PrimaryColor)
            rgb = [colorref & 0xFF, (colorref >> 8) & 0xFF, (colorref >> 16) & 0xFF]
        except Exception:
            pass
        try:
            entities = z(rm.GetEntities)
        except Exception:
            entities = None
        if not entities:
            continue
        for ent in entities:
            try:
                area = float(z(ent.GetArea))
                box = z(ent.GetBox)
                key = _appearance_key(area, float(box[0]), float(box[2]))
            except Exception:
                continue
            out.setdefault(key, {"name": name, "rgb": rgb})
    return out


def _select_label_face(body, max_area: float = LABEL_FACE_MAX_AREA):
    best, best_loops, best_area = None, -1, -1.0
    faces = z(body.GetFaces)
    if not faces:
        return None
    for face in faces:
        try:
            area = float(z(face.GetArea))
        except Exception:
            continue
        if area >= max_area:
            continue
        try:
            loops = int(z(face.GetLoopCount))
        except Exception:
            loops = 0
        if loops > best_loops or (loops == best_loops and area > best_area):
            best, best_loops, best_area = face, loops, area
    return best


def measure_logo_faces(body, appearance_map, appearance_name: str = "color") -> dict:
    faces_out, total_area = [], 0.0
    faces = z(body.GetFaces) or []
    for face in faces:
        try:
            area = float(z(face.GetArea))
            box = z(face.GetBox)
            key = _appearance_key(area, float(box[0]), float(box[2]))
        except Exception:
            continue
        found = appearance_map.get(key)
        if not found or found.get("name") != appearance_name:
            continue
        m = face_metrics(face)
        total_area += m["area_m2"]
        faces_out.append({
            "loops": m["loops"],
            "area_m2": m["area_m2"],
            "outline_centroid_m": m["centroid_m"],
            "skew_x": m["skew_x"],
            "skew_z": m["skew_z"],
            "color_rgb": found.get("rgb"),
        })
    return {"face_count": len(faces_out),
            "total_area_m2": total_area,
            "faces": faces_out}


def _wrap_com(obj):
    if obj is None:
        return None
    try:
        import win32com.client
        if isinstance(obj, win32com.client.CDispatch):
            return obj
        return win32com.client.Dispatch(obj)
    except Exception:
        return obj


def _operations2(body_a, body_b, op_type: int = SW_BODY_INTERSECT):
    res = None
    try:
        err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        res, code = body_a.Operations2(op_type, body_b, err), int(err.value)
    except (TypeError, ValueError, AttributeError, pythoncom.com_error):
        out = body_a.Operations2(op_type, body_b, 0)
        if isinstance(out, tuple) and len(out) == 2 and not hasattr(out[1], "__len__"):
            res, code = out[0], int(out[1])
        else:
            res, code = out, 0

    if res:
        res = [_wrap_com(r) for r in res]
    return res, code


def _boxes_overlap(a, b, pad: float = BBOX_PAD) -> bool:
    return (a[0] - pad <= b[3] and b[0] - pad <= a[3] and
            a[1] - pad <= b[4] and b[1] - pad <= a[4] and
            a[2] - pad <= b[5] and b[2] - pad <= a[5])


def measure_interference(bodies, boxes=None, pad: float = BBOX_PAD,
                         progress=None) -> dict:
    n = len(bodies)
    if boxes is None:
        boxes = []
        for b in bodies:
            try:
                boxes.append([float(v) for v in z(b.GetBodyBox)])
            except Exception:
                boxes.append([0.0] * 6)

    candidates = [(i, j) for i in range(n) for j in range(i + 1, n)
                  if _boxes_overlap(boxes[i], boxes[j], pad)]

    pairs, total_volume = [], 0.0
    for done, (i, j) in enumerate(candidates, start=1):
        volume, error_code, result_bodies = 0.0, 0, 0
        try:
            copy_a = z(bodies[i].Copy)
            copy_b = z(bodies[j].Copy)
            res, error_code = _operations2(copy_a, copy_b, SW_BODY_INTERSECT)
            if res:
                result_bodies = len(res)
                for rb in res:
                    try:
                        volume += float(rb.GetMassProperties(0.0)[3])
                    except (IndexError, TypeError, ValueError):
                        pass
        except Exception as exc:
            error_code = -1
            volume = 0.0
            print(f"[INTF-ERR] {i},{j} {exc}")

        if volume > MIN_INTERFERENCE_VOLUME or error_code != 0:
            total_volume += volume
            pairs.append({
                "a": f"b{i:02d}",
                "b": f"b{j:02d}",
                "volume_m3": volume,
                "result_bodies": result_bodies,
                "error_code": error_code,
            })

        if progress:
            progress(done, len(candidates))

    return {"tested_pairs": len(candidates),
            "total_volume_m3": total_volume,
            "pairs": pairs}


def compute_quantiles(values):
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    return {
        "p10": sorted_vals[int(n * 0.1)],
        "p25": sorted_vals[int(n * 0.25)],
        "p50": sorted_vals[int(n * 0.5)],
        "p75": sorted_vals[int(n * 0.75)],
        "p90": sorted_vals[int(n * 0.9)],
    }


def cluster_bodies(bodies, epsilon: float):
    """Simple density-based clustering for bodies based on centroid distance."""
    if not bodies:
        return []

    n = len(bodies)
    visited = set()
    clusters = []

    for i in range(n):
        if i in visited:
            continue

        cluster = []
        queue = [i]
        visited.add(i)

        while queue:
            curr = queue.pop(0)
            cluster.append(bodies[curr])

            ca = bodies[curr]["centroid_m"]
            for neighbor in range(n):
                if neighbor in visited:
                    continue

                cb = bodies[neighbor]["centroid_m"]
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ca, cb)))

                if dist < epsilon:
                    visited.add(neighbor)
                    queue.append(neighbor)

        if cluster:
            clusters.append(cluster)

    return clusters



from __future__ import annotations

from common import solidworks_measure as M
from common.solidworks_measure import z

SW_SOLID_BODY = 0


def _colour_distance(a, b):
    if not a or not b:
        return None
    return max(abs(u - v) for u, v in zip(a, b))


def _shortlist_faces(bodies, appearance_map, max_area):
    out = []
    for idx, body in enumerate(bodies):
        faces = z(body.GetFaces)
        if not faces:
            continue
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
            try:
                box = z(face.GetBox)
                key = M._appearance_key(area, float(box[0]), float(box[2]))
            except Exception:
                key = None
            app = appearance_map.get(key, {}) if key else {}
            out.append((idx, face, app.get("rgb"), area, loops,
                        app.get("name", "")))
    return out


def discover_glyphs(bodies, appearance_map, seed_labels,
                    colour_tol=24, area_tol_frac=0.35,
                    max_area=M.LABEL_FACE_MAX_AREA):
    shortlist = _shortlist_faces(bodies, appearance_map, max_area)
    labels, taken = [], set()

    for seed in seed_labels:
        want_rgb = seed.get("color_rgb")
        best, best_cost = None, None
        for (idx, face, rgb, area, loops, appname) in shortlist:
            if id(face) in taken:
                continue
            cd = _colour_distance(want_rgb, rgb)
            if cd is None or cd > colour_tol:
                continue
            da = abs(area - seed["area_m2"]) / max(seed["area_m2"], 1e-30)
            cost = (cd, da)
            if best_cost is None or cost < best_cost:
                best, best_cost = (idx, face, area, loops), cost

        confidence = "colour"
        if best is None:
            for (idx, face, rgb, area, loops, appname) in shortlist:
                if id(face) in taken:
                    continue
                if loops != seed["loops"]:
                    continue
                da = abs(area - seed["area_m2"]) / max(seed["area_m2"], 1e-30)
                if da > area_tol_frac:
                    continue
                if best_cost is None or (da,) < best_cost:
                    best, best_cost = (idx, face, area, loops), (da,)
            confidence = "loops+area (no colour - LOW CONFIDENCE)"

        if best is None:
            labels.append({"glyph": seed["glyph"], "found": False,
                           "error": "no face matched colour, loops or area"})
            continue

        idx, face, area, loops = best
        taken.add(id(face))
        m = M.face_metrics(face)
        entry = {
            "glyph": seed["glyph"], "found": True,
            "host_body": f"c{idx:02d}", "loops": m["loops"],
            "area_m2": m["area_m2"],
            "outline_centroid_m": m["centroid_m"],
            "skew_x": m["skew_x"], "skew_z": m["skew_z"],
            "outline_samples": m["outline_samples"],
            "identified_by": confidence,
        }
        app = appearance_map.get(
            M._appearance_key(m["area_m2"], m["_box_xmin"], m["_box_zmin"]), {})
        entry["appearance"] = app.get("name", "")
        entry["color_rgb"] = app.get("rgb")
        if m["loops"] != seed["loops"]:
            entry["loop_count_changed"] = [seed["loops"], m["loops"]]
        labels.append(entry)
    return labels


def feature_census(doc, rebuild="edit"):
    try:
        import pythoncom
        from win32com.client import VARIANT
    except ImportError:
        pythoncom = VARIANT = None

    if rebuild:
        try:
            if rebuild == "force":
                doc.ForceRebuild3(False)
            else:
                z(doc.EditRebuild3)
        except Exception as exc:
            return None, {"note": f"rebuild call failed: {exc}"}

    census, nfeat = {}, 0
    feat = z(doc.FirstFeature)
    while feat is not None:
        nfeat += 1
        try:
            if VARIANT is None:
                raise RuntimeError("no pywin32")
            warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
            code = feat.GetErrorCode2(warn)
            is_warning = bool(warn.value)
        except Exception:
            code, is_warning = z(feat.GetErrorCode), False
        if code:
            census[str(feat.Name)] = [int(code), is_warning]
        feat = z(feat.GetNextFeature)
    return census, {"features": nfeat}


def health_gate(doc, baseline=None):
    census, meta = feature_census(doc)
    if census is None:
        return {"ok": False, "errors": -1, "broken_features": [], **meta}

    nfeat = meta.get("features", 0)
    hard = {n: c for n, (c, w) in census.items() if not w}
    warn = {n: c for n, (c, w) in census.items() if w}

    seed = (baseline or {}).get("rebuild", {}).get("feature_errors")
    if seed is None:
        return {"ok": not hard, "errors": len(hard), "warnings": len(warn),
                "features": nfeat,
                "broken_features": [{"name": n, "code": c}
                                    for n, c in sorted(hard.items())[:25]],
                "graded": "absolute",
                "note": "no seed rebuild census in the baseline - graded "
                        "against zero, which this seed cannot pass. Run "
                        "harness.py --capture-seed-rebuild with the seed "
                        "open to record it."}

    seed_hard = {n for n, v in seed.items() if not v[1]}
    seed_any = set(seed)

    newly_broken = [{"name": n, "code": c} for n, c in sorted(hard.items())
                    if n not in seed_hard]
    new_warnings = [{"name": n, "code": c} for n, c in sorted(warn.items())
                    if n not in seed_any]
    repaired = sorted(n for n in seed_hard if n not in hard)

    return {
        "ok": not newly_broken,
        "errors": len(newly_broken),
        "features": nfeat,
        "graded": "delta_vs_seed",
        "broken_features": newly_broken[:25],
        "new_warnings": new_warnings[:25],
        "pre_existing_errors": len(seed_hard),
        "pre_existing_still_present": len(seed_hard & set(hard)),
        "repaired": repaired[:25],
        "note": (f"{len(newly_broken)} newly broken vs the seed's "
                 f"{len(seed_hard)} pre-existing; "
                 f"{len(new_warnings)} new warnings (reported, not failed)"),
    }


def capture_bodies(doc):
    raw = doc.GetBodies2(SW_SOLID_BODY, False)
    raw = list(raw) if raw else []
    bodies, boxes = [], []
    gmin, gmax = [1e30] * 3, [-1e30] * 3
    for idx, b in enumerate(raw):
        mp = b.GetMassProperties(0.0)
        box = [float(v) for v in z(b.GetBodyBox)]
        boxes.append(box)
        for k in range(3):
            gmin[k] = min(gmin[k], box[k])
            gmax[k] = max(gmax[k], box[k + 3])
        bodies.append({
            "id": f"c{idx:02d}", "name": str(b.Name),
            "centroid_m": [float(mp[0]), float(mp[1]), float(mp[2])],
            "volume_m3": float(mp[3]), "area_m2": float(mp[4]),
            "inertia_com": {"Ixx": float(mp[6]), "Iyy": float(mp[7]),
                            "Izz": float(mp[8]), "Ixy": float(mp[9]),
                            "Izx": float(mp[10]), "Iyz": float(mp[11])},
            "bbox_m": box,
        })
    return raw, bodies, boxes, gmin, gmax


def renumber_interference(intf):
    out = dict(intf)
    out["pairs"] = [{**p, "a": "c" + p["a"][1:], "b": "c" + p["b"][1:]}
                    for p in intf.get("pairs", [])]
    return out


def capture(baseline, doc=None, width_axis=0, progress=None,
            label_face_max_area=None, label_colour_tol=24,
            label_area_tol_frac=0.35):
    if doc is None:
        from common import solidworks_session as sws
        doc = sws.active_doc(sws.attach())
        if doc is None:
            raise RuntimeError("no active SolidWorks document")

    max_area = (label_face_max_area if label_face_max_area is not None
                else M.LABEL_FACE_MAX_AREA)

    rebuild = health_gate(doc, baseline)
    raw, bodies, boxes, gmin, gmax = capture_bodies(doc)
    amap = M.build_appearance_map(doc)
    labels = discover_glyphs(raw, amap, baseline.get("labels", []),
                             colour_tol=label_colour_tol,
                             area_tol_frac=label_area_tol_frac,
                             max_area=max_area)
    intf = renumber_interference(
        M.measure_interference(raw, boxes=boxes, progress=progress))

    return {
        "document": str(z(doc.GetTitle)),
        "rebuild": rebuild,
        "global": {"bbox_m": gmin + gmax,
                   "width_m": gmax[width_axis] - gmin[width_axis]},
        "bodies": bodies,
        "labels": [l for l in labels if l.get("found")],
        "labels_missing": [l for l in labels if not l.get("found")],
        "interference": intf,
        "appearance_faces_mapped": len(amap),
    }

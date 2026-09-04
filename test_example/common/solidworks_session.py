"""Shared talking-to-SolidWorks plumbing (Windows + pywin32 only).

The transport layer every SolidWorks harness shares: attaching to the
running application, finding/opening/activating documents, byref VARIANT
helpers for the API's out-parameters, and the `z()` null-safe call idiom.
Measurement logic stays with each harness (or solidworks_capture/measure);
this module only owns the session.

UNVALIDATED ON WINDOWS: written on a non-Windows machine; run
SolidWorks/VALIDATION.md before trusting grades that flow through it.
"""
from __future__ import annotations

import os

try:
    import pythoncom
    import pywintypes
    import win32com.client
    import win32com.client.dynamic
    from win32com.client import VARIANT
except ImportError:          # non-Windows: importable, unusable
    pythoncom = None
    pywintypes = None
    win32com = None
    VARIANT = None

# swDocumentTypes_e
DOC_PART = 1
DOC_ASSEMBLY = 2
# swOpenDocOptions_e
OPEN_SILENT = 1
OPEN_READONLY = 2
# swRebuildOnActivation_e
DONT_REBUILD_ON_ACTIVATE = 1
# swSaveAsVersion_e / swSaveAsOptions_e
SAVE_AS_CURRENT = 0
SAVE_AS_SILENT = 1


def z(member):
    """Call `member` if callable, tolerating COM members that surface as
    either properties or methods depending on the typelib state."""
    if not callable(member):
        return member
    try:
        return member()
    except Exception:
        return member


def byref_i4(value=0):
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, value)


def byref_bool(value=False):
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, value)


def redispatch(obj):
    """Re-wrap a COM object so late-bound attribute access works on
    interfaces win32com typed too narrowly (PartDoc methods on a
    ModelDoc2, specific features, ...)."""
    if obj is None:
        return None
    if isinstance(obj, win32com.client.CDispatch):
        return win32com.client.Dispatch(obj._oleobj_)
    return win32com.client.Dispatch(obj)


def attach():
    """The running SolidWorks application, or raise. Never launches one:
    grading against a live session is the contract for these tasks.

    Deliberately forces DYNAMIC (late-bound) dispatch via
    win32com.client.dynamic.Dispatch on the raw IDispatch pointer, instead
    of win32com.client.GetActiveObject. GetActiveObject auto-upgrades to
    EARLY-bound dispatch whenever a makepy'd typelib module is sitting in
    the gen_py cache (which sw_constant()'s named-constant lookup requires
    and creates) -- and early-bound [in,out] byref params use a different
    calling convention (extra values in the return tuple) than the
    VARIANT(VT_BYREF, ...) idiom byref_i4/byref_bool implement below.
    Confirmed on the grading box 2026-08-15: once gen_py had the SldWorks
    typelib cached, plain GetActiveObject silently early-bound and
    OpenDoc6/GetErrorCode2/SaveAs2 broke with
    "TypeError: int() argument must be ... not 'VARIANT'". Forcing dynamic
    dispatch here keeps the byref helpers correct regardless of whether
    gen_py has been populated (by us or anything else on the box)."""
    if win32com is None:
        raise RuntimeError("pywin32 not available - SolidWorks harnesses "
                           "run only on Windows")
    try:
        clsid = pywintypes.IID("SldWorks.Application")
        raw = pythoncom.GetActiveObject(clsid).QueryInterface(
            pythoncom.IID_IDispatch)
        return win32com.client.dynamic.Dispatch(raw)
    except Exception as exc:
        raise RuntimeError(
            "could not attach to a running SolidWorks (is it open?): "
            f"{exc}") from exc


def active_doc(app):
    return z(app.ActiveDoc)


def find_document(app, path):
    """An already-open document with the given path, or None."""
    target = str(path).lower()
    doc = z(app.GetFirstDocument)
    while doc is not None:
        try:
            if str(z(doc.GetPathName) or "").lower() == target:
                return doc
        except Exception:
            pass
        try:
            doc = z(doc.GetNext)
        except Exception:
            break
    return None


def dyn(obj):
    """Force dynamic (late-bound) dispatch on a COM object -- every
    harness was independently reimplementing this (as a local `_dyn`)
    because plain win32com.client.Dispatch() (which redispatch() above
    uses) silently upgrades to early-bound whenever a makepy'd typelib
    is sitting in the gen_py cache, exposing a different interface
    surface on some objects (e.g. IBody2 without GetFaces) and breaking
    the VARIANT(VT_BYREF, ...) byref idiom -- same landmine attach()'s
    docstring documents for the top-level Application object."""
    if obj is None:
        return None
    raw = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
    return win32com.client.dynamic.Dispatch(raw)


def close_all_documents(app):
    """Close every document currently open in this SolidWorks session.

    SolidWorks resolves referenced components by filename, not full
    path. Tasks whose environment/solution/examples folders each carry
    their own same-named copies of shared component files (a common
    pattern in this repo) hit a real bug because of this: if a document
    stays open from grading a previous candidate, the next assembly
    that references a component with that same filename silently
    reuses the stale already-open document instead of loading its own
    folder's copy, even though the assembly itself opens from the
    correct path. Confirmed live on 30_shampoo_bottle: this produced
    byte-identical geometry measurements across every adversarial
    example. Sweeping the whole session closed before every fresh open
    (see open_document) removes the possibility entirely -- a blunt
    instrument, but fine for harnesses that only ever need one
    assembly open at a time."""
    docs = []
    doc = z(app.GetFirstDocument)
    while doc is not None:
        docs.append(dyn(doc))
        try:
            doc = z(doc.GetNext)
        except Exception:
            break
    for d in docs:
        try:
            app.CloseDoc(z(d.GetTitle))
        except Exception:
            pass


def open_document(app, path, doc_type=None, options=OPEN_SILENT):
    """(doc, opened_here). Reuses an already-open document with this
    exact path if found (e.g. the live document being graded);
    otherwise sweeps the session closed first (see close_all_documents)
    so no stale same-named component from a previous candidate/baseline
    can leak into this one, then opens fresh. doc_type defaults to
    inferring swDocASSEMBLY/swDocPART from the file extension."""
    doc = find_document(app, path)
    if doc is not None:
        return dyn(doc), False
    close_all_documents(app)
    if doc_type is None:
        doc_type = (DOC_ASSEMBLY if str(path).lower().endswith(".sldasm")
                    else DOC_PART)
    errs, warns = byref_i4(), byref_i4()
    doc = app.OpenDoc6(os.path.abspath(str(path)), doc_type, options, "",
                       errs, warns)
    if doc is None:
        raise RuntimeError(f"could not open {path}")
    return dyn(doc), True


def open_part_readonly_invisible(app, path):
    """Open a part silently, read-only, and invisible (so it never steals
    the user's active window); visibility preference is restored."""
    existing = find_document(app, path)
    if existing is not None:
        return existing
    try:
        app.DocumentVisible(False, DOC_PART)
    except Exception:
        pass
    try:
        errs, warns = byref_i4(), byref_i4()
        return app.OpenDoc6(path, DOC_PART, OPEN_SILENT | OPEN_READONLY,
                            "", errs, warns)
    finally:
        try:
            app.DocumentVisible(True, DOC_PART)
        except Exception:
            pass


def activate(app, doc):
    """Make `doc` the active document without rebuilding it."""
    try:
        err = byref_i4()
        app.ActivateDoc3(z(doc.GetTitle), False,
                         DONT_REBUILD_ON_ACTIVATE, err)
    except Exception:
        pass


def sw_constant(name, fallback):
    """A named constant from the generated typelib module when available
    (win32com.client.constants requires a makepy'd SolidWorks typelib),
    else the documented numeric fallback.

    NOTE: attach() uses GetActiveObject, which returns a late-bound
    dispatch that does NOT populate win32com.client.constants on its own
    (unlike EnsureDispatch/makepy-run processes) -- so in practice this
    almost always falls through to `fallback`. Keep fallbacks verified
    against the real typelib, not guessed."""
    try:
        return getattr(win32com.client.constants, name)
    except Exception:
        return fallback

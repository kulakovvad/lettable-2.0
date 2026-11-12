"""TraceLease UI. Run: streamlit run app/streamlit_app.py"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import data  # noqa: E402
from app.pages_ import changes, documents, evaluation, proforma, review  # noqa: E402

st.set_page_config(page_title="TraceLease", layout="wide")


def _guard(name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        st.error(f"{name} could not render: {type(exc).__name__}: {exc}")


def _sidebar() -> tuple[str, str | None, str, list[str]]:
    docs = data.documents()
    if not docs:
        st.sidebar.error("No documents in the database. Run: python -m scripts.load_fixtures")
        st.stop()
    by_id = {d.id: d for d in docs}
    doc_id = st.sidebar.selectbox(
        "Document", list(by_id), key="doc_id",
        format_func=lambda i: f"{i} · {by_id[i].filename.split('_', 1)[-1][:40]}")
    st.sidebar.caption(f"{by_id[doc_id].page_count} pages · sha256 {by_id[doc_id].sha256[:12]}…")

    versions = data.versions(doc_id)
    pending = st.session_state.pop("pending_version", None)
    ids = [v.id for v in versions]
    if pending in ids:
        st.session_state["version_id"] = pending
    elif ids and st.session_state.get("version_id") not in ids:
        st.session_state["version_id"] = ids[-1]
    version_id = None
    if versions:
        labels = {v.id: f"v{v.version_no} · {v.author} · {v.reason or ''}"[:60] for v in versions}
        version_id = st.sidebar.selectbox("Proforma version", ids, format_func=labels.get,
                                          key="version_id")
    else:
        st.sidebar.caption("No proforma version yet.")

    author = st.sidebar.text_input("Analyst name (author of edits)", value=data.default_author(),
                                   key="author")
    scope = st.sidebar.radio("Evaluation scope", ["this document", "all documents"], key="eval_scope_docs")
    eval_docs = [doc_id] if scope == "this document" else list(by_id)

    with st.sidebar.expander("Adapters"):
        for k, v in data.backend_status().items():
            st.caption(f"{k}: {v}")
        for w in data.warnings:
            st.caption(f"warning: {w}")
    return doc_id, version_id, author, eval_docs


def main() -> None:
    st.title("TraceLease — every number traced to its source")
    if os.getenv("DEMO_MODE") == "1":
        st.badge("Cached run, no live model calls", color="blue")
    try:
        doc_id, version_id, author, eval_docs = _sidebar()
    except Exception as exc:
        st.error(f"Cannot reach the database: {type(exc).__name__}: {exc}. "
                 "Check DATABASE_URL in .env and that Postgres is running.")
        st.stop()
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    tabs = st.tabs(["Documents & fields", "Evaluation", "Proforma & Explain", "Review queue", "Changes"])
    with tabs[0]:
        _guard("Documents & fields", lambda: documents.render(doc_id))
    with tabs[1]:
        _guard("Evaluation", lambda: evaluation.render(eval_docs))
    with tabs[2]:
        _guard("Proforma", lambda: proforma.render(doc_id, version_id, author))
    with tabs[3]:
        _guard("Review queue", lambda: review.render(doc_id, author))
    with tabs[4]:
        _guard("Changes", lambda: changes.render(doc_id))


main()

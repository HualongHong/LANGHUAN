"""M4a 人工复核界面（Streamlit）。

启动方式（二选一）：
    streamlit run archive_plate_assembler/review_ui/app.py -- <job_dir>
    python -m archive_plate_assembler.review_ui <job_dir>

也可用环境变量 APA_JOB_DIR 指定 job 目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from PIL import Image

from archive_plate_assembler.review_ui.store import (
    STATUS_CONFIRMED,
    STATUS_EDITED,
    STATUS_PENDING,
    TAXONOMY_FIELDS,
    ReviewStore,
)

STATUS_BADGE = {
    STATUS_PENDING: "⬜",
    STATUS_CONFIRMED: "✅",
    STATUS_EDITED: "✏️",
}

FILTER_OPTIONS = ["全部", "仅有分歧", "未复核"]

# (字段名, 标签)；taxonomy 单独渲染
TEXT_FIELDS = ["chinese_name", "scientific_name", "author", "source_book"]


def _get_job_dir() -> str | None:
    import os

    args = [a for a in sys.argv[1:] if a != "--" and not a.startswith("-")]
    if args:
        return args[0]
    return os.environ.get("APA_JOB_DIR")


def _get_store(job_dir: str) -> ReviewStore:
    """按 job_dir 缓存 ReviewStore（st.cache_resource 保证跨 rerun 复用）。"""
    return _cached_store(job_dir)


@st.cache_resource
def _cached_store(job_dir: str) -> ReviewStore:
    return ReviewStore(job_dir)


def _prepare_exports(store: ReviewStore) -> dict[str, bytes]:
    """save 后生成 JSON/CSV/SQLite 导出内容，供下载按钮使用。"""
    store.save()
    job_dir = store.job_dir
    csv_path = job_dir / "review_export.csv"
    sqlite_path = job_dir / "review_export.sqlite"
    store.export_csv(str(csv_path))
    store.export_sqlite(str(sqlite_path))
    return {
        "json": (job_dir / "corrected_species.json").read_bytes(),
        "csv": csv_path.read_bytes(),
        "sqlite": sqlite_path.read_bytes(),
    }


def _label(view) -> str:
    badge = STATUS_BADGE.get(view.status, "⬜")
    return f"{badge} {view.entry_id} {view.record.chinese_name}"


def _current_value(view, field: str):
    if field.startswith("taxonomy."):
        name = field.split(".", 1)[1]
        return getattr(view.record.taxonomy, name)
    return getattr(view.record, field)


def _field_label(view, field: str, base: str) -> str:
    """分歧字段在标签上高亮模型 B 的状态与置信度。"""
    check = view.checks.get(field)
    if check and check.get("status") and check["status"] != "确认":
        return f"⚠️ {base}（B: {check['status']}, 置信度{check.get('confidence', 0):.2f}）"
    return base


def _render_suggestions(view, store: ReviewStore) -> None:
    """模型 B 分歧建议区：展示 value_b/note 并提供采纳按钮。"""
    items = [
        f
        for f in dict.fromkeys(list(view.disagreements) + list(view.checks.keys()))
        if view.checks.get(f, {}).get("status") not in (None, "确认")
    ]
    if not items:
        return
    with st.expander(f"模型 B 分歧与建议（{len(items)} 项）", expanded=True):
        for field in items:
            check = view.checks[field]
            cols = st.columns([4, 1])
            with cols[0]:
                st.info(
                    f"**{field}** — B 状态：{check.get('status')}，"
                    f"置信度 {check.get('confidence', 0):.2f}\n\n"
                    f"B 建议值：`{check.get('value_b')}`\n\n{check.get('note', '')}"
                )
            with cols[1]:
                if check.get("value_b") is not None and st.button(
                    "采纳 B 建议", key=f"accept::{view.entry_id}::{field}"
                ):
                    store.apply_suggestion(view.entry_id, field)
                    st.rerun()


def _render_form(view, store: ReviewStore) -> None:
    """字段编辑表单：st.form 批量提交，避免每次键入都 rerun 写日志。"""
    with st.form(key=f"form::{view.entry_id}"):
        values: dict[str, object] = {}
        for field in TEXT_FIELDS:
            values[field] = st.text_input(
                _field_label(view, field, field),
                value=_current_value(view, field) or "",
                key=f"w::{view.entry_id}::{field}",
            )
        values["page"] = st.number_input(
            _field_label(view, "page", "page"),
            value=int(view.record.page),
            step=1,
            key=f"w::{view.entry_id}::page",
        )
        values["synonyms"] = st.text_area(
            _field_label(view, "synonyms", "synonyms（每行一个）"),
            value="\n".join(view.record.synonyms),
            key=f"w::{view.entry_id}::synonyms",
        )
        st.caption("taxonomy 六阶元")
        tax_fields = list(TAXONOMY_FIELDS)
        for row in range(0, len(tax_fields), 2):
            cols = st.columns(2)
            for col, name in zip(cols, tax_fields[row : row + 2]):
                field = f"taxonomy.{name}"
                with col:
                    values[field] = st.text_input(
                        _field_label(view, field, name),
                        value=_current_value(view, field) or "",
                        key=f"w::{view.entry_id}::{field}",
                    )
        values["description"] = st.text_area(
            _field_label(view, "description", "description"),
            value=view.record.description or "",
            height=200,
            key=f"w::{view.entry_id}::description",
        )
        st.caption("tables（M4a 只读）")
        st.json([t.model_dump(mode="json") for t in view.record.tables])
        submitted = st.form_submit_button("应用修改")

    if submitted:
        changed = 0
        for field, value in values.items():
            if field == "synonyms":
                new = [s.strip() for s in str(value).splitlines() if s.strip()]
                old = view.record.synonyms
            elif field == "page":
                new = int(value)
                old = view.record.page
            else:
                new = value if value != "" else None
                old = _current_value(view, field)
            if new != old:
                store.apply_edit(view.entry_id, field, new)
                changed += 1
        if changed:
            st.toast(f"已应用 {changed} 处修改")
        st.rerun()


def _render_roi_canvas(store: ReviewStore, entry_id: str, page_imgs: list[str]) -> None:
    """M4b 框选裁切：在页面扫描图上拖矩形 → 裁为条目图片。

    依赖 streamlit-image-annotation（Detection 模式）：组件返回的 bbox 已是
    图像原生像素 [xmin, ymin, xmax, ymax]，无需画布缩放换算。缺装时提示降级，
    不影响其余复核功能。裁切语义（越界收拢/退化框过滤/落盘命名）在 roi_tool，
    可单测。
    """
    try:
        from streamlit_image_annotation import detection
    except ImportError:
        st.info("框选裁切（M4b）需安装 streamlit-image-annotation：pip install streamlit-image-annotation")
        return

    from archive_plate_assembler.review_ui.roi_tool import apply_rois

    st.subheader("框选裁切（多物种同图分割 / 抠图兜底）")
    page_path = st.selectbox("框选页面", page_imgs, format_func=lambda p: Path(p).name,
                             key=f"roi_page_{entry_id}")
    st.caption("在图上拖出矩形并选标签「ROI」；点组件内保存按钮后回显，再点下方按钮应用。")
    result = detection(
        page_path,
        label_list=["ROI"],
        bboxes=None,
        labels=None,
        height=560,
        width=720,
        key=f"roi_canvas_{entry_id}_{Path(page_path).name}",
    )
    if st.button("应用框选为条目图片", use_container_width=True,
                 key=f"roi_apply_{entry_id}"):
        boxes = []
        for item in (result or []):
            if item.get("label") != "ROI":
                continue
            x0, y0, x1, y1 = item["bbox"]
            boxes.append((round(x0), round(y0), round(x1 - x0), round(y1 - y0)))
        added = apply_rois(store, entry_id, page_path, boxes)
        if added:
            st.success(f"已新增 {len(added)} 张裁切图；点左侧「保存」写入 corrected_species.json")
            st.rerun()
        else:
            st.warning("没有有效框：请在图上拖出矩形（宽/高不小于 3px）并保存")


def main() -> None:
    st.set_page_config(page_title="APA 人工复核", layout="wide")
    job_dir = _get_job_dir()
    if not job_dir or not (Path(job_dir) / "drafts.json").exists():
        st.error(
            "未找到有效 job 目录。用法：\n\n"
            "```\nstreamlit run archive_plate_assembler/review_ui/app.py -- <job_dir>\n"
            "# 或\npython -m archive_plate_assembler.review_ui <job_dir>\n```"
        )
        return

    store = _get_store(job_dir)
    all_views = store.entries()

    done = sum(1 for v in all_views if v.status != STATUS_PENDING)
    st.progress(done / len(all_views) if all_views else 0.0)
    st.caption(f"复核进度：{done} / {len(all_views)}")

    # ---------------- 侧边栏 ----------------
    with st.sidebar:
        st.header(Path(job_dir).name)
        counts = {s: sum(1 for v in all_views if v.status == s) for s in STATUS_BADGE}
        st.write(
            f"条目总数：{len(all_views)}　已确认：{counts[STATUS_CONFIRMED]}　"
            f"已修改：{counts[STATUS_EDITED]}　未复核：{counts[STATUS_PENDING]}"
        )

        filter_opt = st.radio("过滤", FILTER_OPTIONS, horizontal=True)
        if filter_opt == "仅有分歧":
            views = [v for v in all_views if v.disagreements]
        elif filter_opt == "未复核":
            views = [v for v in all_views if v.status == STATUS_PENDING]
        else:
            views = all_views

        if not views:
            st.warning("当前过滤条件下没有条目")
            return
        ids = [v.entry_id for v in views]
        if st.session_state.get("sel_entry") not in ids:
            st.session_state["sel_entry"] = ids[0]
        idx = ids.index(st.session_state["sel_entry"])

        def _go(delta: int) -> None:
            st.session_state["sel_entry"] = ids[(idx + delta) % len(ids)]

        nav = st.columns(2)
        nav[0].button("上一条", on_click=_go, args=(-1,), use_container_width=True)
        nav[1].button("下一条", on_click=_go, args=(1,), use_container_width=True)

        selected = st.selectbox(
            "条目",
            ids,
            format_func=lambda eid: _label(store.get(eid)),
            key="sel_entry",
        )
        view = store.get(selected)

        if st.button("保存", type="primary", use_container_width=True):
            st.session_state["exports"] = _prepare_exports(store)
            st.success("已保存 corrected_species.json 与 review_state.json")

        if "exports" not in st.session_state:
            st.session_state["exports"] = _prepare_exports(store)
        exports = st.session_state["exports"]
        st.download_button("下载 JSON", exports["json"], "corrected_species.json")
        st.download_button("下载 CSV", exports["csv"], "corrected_species.csv")
        st.download_button("下载 SQLite", exports["sqlite"], "corrected_species.sqlite")

    # ---------------- 主区 ----------------
    left, right = st.columns(2)
    with left:
        st.subheader("页面扫描图")
        page_imgs = store.page_images(view.entry_id)
        if page_imgs:
            for col, path in zip(st.columns(len(page_imgs)), page_imgs):
                col.image(path, caption=Path(path).name, use_container_width=True)
            _render_roi_canvas(store, view.entry_id, page_imgs)
        else:
            st.info("无页面扫描图")
        st.subheader("处理后图片")
        entry_imgs = store.entry_images(view.entry_id)
        if entry_imgs:
            for col, path in zip(st.columns(len(entry_imgs)), entry_imgs):
                col.image(path, caption=Path(path).name, use_container_width=True)
        else:
            st.info("无条目图片")

    with right:
        st.subheader(f"{view.entry_id} {view.record.chinese_name}")
        _render_suggestions(view, store)
        _render_form(view, store)
        if st.button("确认本条", type="primary"):
            store.mark_confirmed(view.entry_id)
            st.rerun()


main()

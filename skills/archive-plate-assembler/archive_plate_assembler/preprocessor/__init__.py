"""节点 1：页面预处理。

PDF 渲染（≥300 DPI）、页码索引、纯图页/图文混合页分离。
"""

from .render import infer_page_start, load_pages_index, render_pdf

__all__ = ["infer_page_start", "load_pages_index", "render_pdf"]

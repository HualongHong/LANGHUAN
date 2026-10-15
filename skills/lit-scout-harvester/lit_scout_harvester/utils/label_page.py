"""人工标注复核页：单文件 HTML 标注页的载荷构建与渲染。

给研究者一个离线双击可用的单文件 HTML 页面，对文献逐条做字段标注
（或在与 AI 预测对照的 adjudication 模式下核对修改），产出 gold
JSONL 喂给 ``scripts/evaluate_schema.py --gold`` 完成可靠性验证闭环。

设计要点：

* ``build_label_payload`` 把 records / schema / option_sets / preds
  归一成纯 JSON 载荷；控件类型按字段类型自适应——封闭域字段
  （option_set 命中）走单选/多选选择题，布尔/判断语义字段走判断题，
  开放 list[str] 字段走多值标签输入，其余标量走单行文本。
* ``render_label_html`` 把载荷渲染为内联 CSS/JS 的单文件 HTML，
  零外部 CDN 引用（离线可用），localStorage 按内容哈希键自动保存。
* ``gold_records_from_labels`` 是导出规则的 Python 参照实现
  （页面内 JS 与之保持一致）：list 字段一律数组、未标字段空数组、
  标量字段空字符串，record_id 始终保留。测试用它对
  ``evaluate_schema._load_jsonl`` 做往返验证。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

WIDGET_CHOICE_SINGLE = "choice_single"
WIDGET_CHOICE_MULTI = "choice_multi"
WIDGET_JUDGMENT = "judgment"
WIDGET_TEXT = "text"  # 开放 list[str]，多值逐条添加
WIDGET_LINE = "line"  # 非 list 标量，单行文本

_JUDGMENT_TRUE = "true"
_JUDGMENT_FALSE = "false"

_LIST_TYPE_PREFIX = "list["


def _is_list_type(type_name: str) -> bool:
    return str(type_name).strip().lower().startswith(_LIST_TYPE_PREFIX)


def _is_judgment_field(field: Dict[str, Any]) -> bool:
    """布尔/判断语义字段：type 含 bool，或显式 ``"widget": "judgment"``。"""
    type_name = str(field.get("type", "")).strip().lower()
    if "bool" in type_name:
        return True
    return str(field.get("widget", "")).strip().lower() == WIDGET_JUDGMENT


def _option_labels(option_set: Any) -> List[str]:
    """从 option-set 工件（artifact dict）或裸选项列表归一出标签列表。"""
    if option_set is None:
        return []
    raw: Any = option_set
    if isinstance(option_set, dict):
        raw = option_set.get("options", [])
    labels: List[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str):
                label = item.strip()
            elif isinstance(item, dict):
                label = str(item.get("label", "")).strip()
            else:
                label = ""
            if label and label not in labels:
                labels.append(label)
    return labels


def _coerce_list_value(value: Any) -> List[str]:
    """把 preds 里的字段值规范成字符串数组（None/标量/数组均可）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_scalar_value(value: Any) -> str:
    """把 preds 里的标量字段值规范成字符串（布尔归一为 true/false）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return _JUDGMENT_TRUE if value else _JUDGMENT_FALSE
    return str(value).strip()


def _resolve_widget(
    field: Dict[str, Any], option_sets: Optional[Dict[str, Any]]
) -> str:
    """控件判定：option_set 命中 → 选择题；布尔 → 判断题；list → 多值文本。"""
    name = str(field.get("name", ""))
    if option_sets and name in option_sets:
        if field.get("multiLabel"):
            return WIDGET_CHOICE_MULTI
        return WIDGET_CHOICE_SINGLE
    if _is_judgment_field(field):
        return WIDGET_JUDGMENT
    if _is_list_type(str(field.get("type", ""))):
        return WIDGET_TEXT
    return WIDGET_LINE


def _resolve_max_select(field: Dict[str, Any], widget: str) -> Optional[int]:
    """choice_multi 限选数：modeN 优先，退回 max_items。"""
    if widget != WIDGET_CHOICE_MULTI:
        return None
    for key in ("modeN", "max_items", "maxItems"):
        value = field.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def build_label_payload(
    records: Sequence[Dict[str, Any]],
    schema: Dict[str, Any],
    option_sets: Optional[Dict[str, Any]] = None,
    preds: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """把标注任务归一成纯 JSON 载荷（可序列化、可嵌进 HTML）。

    Args:
        records: 每项至少 ``{"record_id", "title", "abstract"}``。
        schema: schema JSON（含 ``fields[]``：name/type/description/
            guidelines[]/examples[]/max_items/multiLabel/modeN）。
        option_sets: 字段名 → option-set 工件（或裸选项列表）。
            命中的字段按封闭域选择题渲染。
        preds: AI 预测记录列表（每项含 ``record_id`` 与各字段值）。
            提供即进入 adjudication 模式：控件预填 AI 值，人工修改后
            与 AI 值不同的字段高亮角标。

    Returns:
        载荷 dict，含 ``version / content_hash / adjudication /
        records / fields``。fields 每项含 name/description/guidelines/
        examples/widget/options/max_select/is_list/ai。
    """
    option_sets = option_sets or {}
    pred_by_id: Dict[Any, Dict[str, Any]] = {}
    if preds:
        for rec in preds:
            rid = rec.get("record_id")
            if rid is not None:
                pred_by_id[str(rid)] = rec

    payload_fields: List[Dict[str, Any]] = []
    for field in schema.get("fields", []):
        name = str(field.get("name", "")).strip()
        if not name:
            continue
        widget = _resolve_widget(field, option_sets)
        is_list = _is_list_type(str(field.get("type", "")))
        ai: Optional[Dict[str, Any]] = None
        if preds:
            ai = {}
            for rid, rec in pred_by_id.items():
                if name not in rec:
                    continue
                raw = rec.get(name)
                ai[rid] = _coerce_list_value(raw) if is_list else _coerce_scalar_value(raw)
        payload_fields.append(
            {
                "name": name,
                "description": str(field.get("description", "") or ""),
                "guidelines": [str(g) for g in (field.get("guidelines") or [])],
                "examples": [str(e) for e in (field.get("examples") or [])],
                "widget": widget,
                "options": _option_labels(option_sets.get(name)),
                "max_select": _resolve_max_select(field, widget),
                "is_list": is_list,
                "ai": ai,
            }
        )

    payload_records = [
        {
            "record_id": str(rec.get("record_id", "")),
            "title": str(rec.get("title", "") or ""),
            "abstract": str(rec.get("abstract", "") or ""),
        }
        for rec in records
    ]

    fingerprint = json.dumps(
        {
            "records": payload_records,
            "fields": [
                {
                    "name": f["name"],
                    "widget": f["widget"],
                    "options": f["options"],
                    "max_select": f["max_select"],
                    "is_list": f["is_list"],
                }
                for f in payload_fields
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

    return {
        "version": 1,
        "content_hash": content_hash,
        "adjudication": bool(preds),
        "records": payload_records,
        "fields": payload_fields,
    }


def gold_records_from_labels(
    labels: Dict[str, Dict[str, Any]],
    fields: Sequence[Dict[str, Any]],
    record_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """标注状态 → gold 记录列表（导出规则的 Python 参照实现）。

    Args:
        labels: ``{record_id: {field_name: value}}``（页面 localStorage
            中的标注态；缺项视为未标）。
        fields: ``build_label_payload`` 产出的 fields 列表（用 name 与
            is_list 决定形状）。
        record_ids: 导出的记录顺序；None 时取 labels 的键序。

    Returns:
        每项 ``{"record_id": ..., "<field>": [...] 或 str}``；list 字段
        一律字符串数组（未标为空数组），标量字段为字符串（未标为空串）。
    """
    ordered_ids = [str(r) for r in record_ids] if record_ids is not None else [
        str(rid) for rid in labels.keys()
    ]
    gold: List[Dict[str, Any]] = []
    for rid in ordered_ids:
        per_record = labels.get(rid, {})
        row: Dict[str, Any] = {"record_id": rid}
        for field in fields:
            name = field["name"]
            raw = per_record.get(name)
            if field.get("is_list"):
                row[name] = _coerce_list_value(raw)
            else:
                row[name] = _coerce_scalar_value(raw)
        gold.append(row)
    return gold


_CSS = """
:root{--bg:#f6f7f9;--panel:#ffffff;--fg:#1f2328;--muted:#6a737d;--line:#d8dce2;
--accent:#1f6feb;--ok:#1a7f37;--partial:#9a6700;--todo:#8c959f;--warn:#cf222e;
--chip:#eaeef2;--badge:#fff0f0;--bdgfg:#cf222e;}
@media (prefers-color-scheme: dark){
:root{--bg:#0d1117;--panel:#161b22;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;
--accent:#58a6ff;--ok:#3fb950;--partial:#d29922;--todo:#8b949e;--warn:#f85149;
--chip:#21262d;--badge:#3d1d1f;--bdgfg:#ff7b72;}}
*{box-sizing:border-box;}
body{margin:0;font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
background:var(--bg);color:var(--fg);font-size:14px;}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:10px 16px;
background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9;}
header .title{font-weight:600;font-size:16px;}
header .title .mode{font-size:12px;font-weight:normal;color:var(--accent);
border:1px solid var(--accent);border-radius:10px;padding:1px 8px;margin-left:8px;}
.progress-wrap{display:flex;align-items:center;gap:8px;color:var(--muted);}
.bar{width:160px;height:8px;background:var(--chip);border-radius:4px;overflow:hidden;}
.bar>div{height:100%;background:var(--ok);width:0;}
.actions{margin-left:auto;display:flex;gap:8px;}
button{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:6px;
background:var(--chip);color:var(--fg);padding:5px 12px;}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;}
button:disabled{opacity:.5;cursor:not-allowed;}
.layout{display:flex;min-height:calc(100vh - 58px);}
aside{width:300px;flex:none;border-right:1px solid var(--line);background:var(--panel);
overflow-y:auto;max-height:calc(100vh - 58px);position:sticky;top:58px;}
.rec-item{display:flex;gap:8px;align-items:flex-start;padding:8px 10px;
border-bottom:1px solid var(--line);cursor:pointer;}
.rec-item:hover{background:var(--chip);}
.rec-item.active{background:var(--chip);outline:2px solid var(--accent);outline-offset:-2px;}
.dot{flex:none;width:10px;height:10px;border-radius:50%;margin-top:4px;background:var(--todo);}
.dot.done{background:var(--ok);}
.dot.partial{background:var(--partial);}
.rec-idx{color:var(--muted);flex:none;margin-top:1px;}
.rec-title{line-height:1.35;}
main{flex:1;padding:16px 24px;max-width:980px;}
.rec-head h2{font-size:17px;margin:0 0 8px;}
.rec-head .abstract{background:var(--panel);border:1px solid var(--line);
border-radius:8px;padding:10px 12px;max-height:220px;overflow-y:auto;
color:var(--muted);line-height:1.6;margin-bottom:16px;white-space:pre-wrap;}
.field{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:12px 14px;margin-bottom:14px;}
.field-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.field-name{font-weight:600;font-family:Consolas,monospace;}
.field-kind{font-size:12px;color:var(--muted);}
.badge{font-size:12px;color:var(--bdgfg);background:var(--badge);
border-radius:10px;padding:1px 8px;display:none;}
.badge.on{display:inline-block;}
.field-desc{color:var(--muted);margin:6px 0;line-height:1.5;}
details.guidelines{margin:6px 0;font-size:13px;color:var(--muted);}
details.guidelines summary{cursor:pointer;color:var(--accent);}
details.guidelines ul{margin:6px 0 2px;padding-left:20px;}
.chips{margin:6px 0;display:flex;flex-wrap:wrap;gap:6px;align-items:center;}
.chips .lbl{font-size:12px;color:var(--muted);}
.chip{font-size:12px;padding:2px 10px;border-radius:12px;background:var(--chip);}
.tag-box{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
.tag{display:inline-flex;align-items:center;gap:6px;background:var(--chip);
border-radius:12px;padding:2px 6px 2px 10px;font-size:13px;}
.tag button{border:none;background:none;color:var(--muted);padding:0 4px;font-size:14px;}
.tag-input{display:flex;gap:8px;}
.tag-input input,.line-input{flex:1;font:inherit;padding:6px 10px;border-radius:6px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);}
.choice-list{margin:6px 0;display:flex;flex-direction:column;gap:4px;}
.choice-list label{display:flex;gap:8px;align-items:center;padding:4px 6px;
border-radius:6px;cursor:pointer;}
.choice-list label:hover{background:var(--chip);}
.counter{font-size:12px;color:var(--muted);}
.counter.over{color:var(--warn);}
.empty{color:var(--muted);font-size:12px;padding:4px 0;}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
background:var(--fg);color:var(--bg);padding:8px 18px;border-radius:8px;
display:none;z-index:99;font-size:13px;}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);padding:8px 12px;
border-bottom:1px solid var(--line);}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;}
"""

_JS = r"""
(function(){
"use strict";
var P = JSON.parse(document.getElementById("lsh-payload").textContent);
var STORE_KEY = "lsh-label:" + P.content_hash;
var fields = P.fields;
var fieldByName = {};
fields.forEach(function(f){ fieldByName[f.name] = f; });
var currentIdx = 0;
var store = loadStore();

function loadStore(){
  try{
    var raw = localStorage.getItem(STORE_KEY);
    if(raw){ return JSON.parse(raw) || {}; }
  }catch(e){}
  return {};
}
function saveStore(){
  try{ localStorage.setItem(STORE_KEY, JSON.stringify(store)); }
  catch(e){ toast("localStorage 写入失败：" + e.message); }
}
function clone(v){ return JSON.parse(JSON.stringify(v)); }
function isEmptyVal(f, v){
  if(f.is_list){ return !v || v.length === 0; }
  return v === null || v === undefined || String(v).trim() === "";
}
function effectiveValue(f, rid){
  var rec = store[rid];
  if(rec && Object.prototype.hasOwnProperty.call(rec, f.name)){
    return clone(rec[f.name]);
  }
  if(f.ai && Object.prototype.hasOwnProperty.call(f.ai, rid)){
    return clone(f.ai[rid]);
  }
  return f.is_list ? [] : "";
}
function listEq(a, b){
  a = (a || []).map(function(x){return String(x).trim();});
  b = (b || []).map(function(x){return String(x).trim();});
  if(a.length !== b.length){ return false; }
  var seen = b.slice();
  for(var i = 0; i < a.length; i++){
    var k = seen.indexOf(a[i]);
    if(k < 0){ return false; }
    seen.splice(k, 1);
  }
  return true;
}
function valueEq(f, a, b){
  if(f.is_list){ return listEq(a, b); }
  return String(a == null ? "" : a).trim() === String(b == null ? "" : b).trim();
}
function fieldLabeled(f, rid){ return !isEmptyVal(f, effectiveValue(f, rid)); }
function recordStatus(rid){
  var done = 0;
  for(var i = 0; i < fields.length; i++){
    if(fieldLabeled(fields[i], rid)){ done++; }
  }
  if(done === 0){ return "todo"; }
  if(done === fields.length){ return "done"; }
  return "partial";
}
function setValue(rid, fname, value){
  var f = fieldByName[fname];
  if(f && f.is_list && typeof value === "string"){
    value = value ? [value] : [];
  }
  if(!store[rid]){ store[rid] = {}; }
  store[rid][fname] = value;
  saveStore();
  renderList();
  updateProgress();
}
function el(tag, cls, text){
  var node = document.createElement(tag);
  if(cls){ node.className = cls; }
  if(text !== null && text !== undefined){ node.textContent = text; }
  return node;
}

/* ---------------- 记录列表 ---------------- */
function renderList(){
  var box = document.getElementById("record-list");
  box.textContent = "";
  P.records.forEach(function(rec, idx){
    var item = el("div", "rec-item" + (idx === currentIdx ? " active" : ""));
    item.appendChild(el("span", "dot " + recordStatus(rec.record_id)));
    item.appendChild(el("span", "rec-idx", (idx + 1) + "."));
    item.appendChild(el("span", "rec-title", rec.title || rec.record_id));
    item.addEventListener("click", function(){
      currentIdx = idx;
      renderAll();
    });
    box.appendChild(item);
  });
}
function updateProgress(){
  var done = 0;
  P.records.forEach(function(rec){
    if(recordStatus(rec.record_id) === "done"){ done++; }
  });
  var text = "已完成 " + done + " / " + P.records.length +
             " · 当前第 " + (currentIdx + 1) + " 条";
  document.getElementById("progress-text").textContent = text;
  document.getElementById("progress-bar").style.width =
    (P.records.length ? (done / P.records.length * 100) : 0) + "%";
}

/* ---------------- 字段控件 ---------------- */
function fieldHead(f, rid){
  var head = el("div", "field-head");
  head.appendChild(el("span", "field-name", f.name));
  var kindMap = {
    choice_single: "封闭域 · 单选", choice_multi: "封闭域 · 多选",
    judgment: "判断题", text: "开放 · 多值", line: "开放 · 单行"
  };
  head.appendChild(el("span", "field-kind", kindMap[f.widget] || f.widget));
  var badge = el("span", "badge", "与 AI 不同");
  badge.setAttribute("data-badge", f.name);
  head.appendChild(badge);
  return head;
}
function extras(f){
  var box = el("div");
  if(f.guidelines && f.guidelines.length){
    var det = el("details", "guidelines");
    det.appendChild(el("summary", "", "标注指引（" + f.guidelines.length + " 条）"));
    var ul = el("ul");
    f.guidelines.forEach(function(g){ ul.appendChild(el("li", "", g)); });
    det.appendChild(ul);
    box.appendChild(det);
  }
  if(f.examples && f.examples.length){
    var chips = el("div", "chips");
    chips.appendChild(el("span", "lbl", "示例（点击填入）："));
    f.examples.forEach(function(ex){
      var chip = el("button", "chip", ex);
      chip.setAttribute("data-example", f.name);
      chip.setAttribute("data-value", ex);
      chips.appendChild(chip);
    });
    box.appendChild(chips);
  }
  return box;
}
function refreshBadge(f, rid){
  var badge = document.querySelector('[data-badge="' + f.name + '"]');
  if(!badge){ return; }
  var on = false;
  if(f.ai && Object.prototype.hasOwnProperty.call(f.ai, rid)){
    on = !valueEq(f, effectiveValue(f, rid), f.ai[rid]);
  }
  badge.className = "badge" + (on ? " on" : "");
}
function renderTagBox(f, rid, wrap){
  wrap.textContent = "";
  var val = effectiveValue(f, rid);
  if(!val.length){
    wrap.appendChild(el("div", "empty", "尚未添加值"));
    return;
  }
  val.forEach(function(v, i){
    var tag = el("span", "tag", v);
    var x = el("button", "", "×");
    x.title = "移除";
    x.addEventListener("click", function(){
      var nv = effectiveValue(f, rid).slice();
      nv.splice(i, 1);
      setValue(rid, f.name, nv);
      renderMain();
    });
    tag.appendChild(x);
    wrap.appendChild(tag);
  });
}
function buildText(f, rid){
  var box = el("div");
  var tagBox = el("div", "tag-box");
  box.appendChild(tagBox);
  var row = el("div", "tag-input");
  var input = el("input");
  input.type = "text";
  input.placeholder = "输入值后回车添加（可多条）";
  input.setAttribute("data-input", f.name);
  var addBtn = el("button", "", "添加");
  function addFromInput(){
    var text = input.value.trim();
    if(!text){ return; }
    var nv = effectiveValue(f, rid).slice();
    if(nv.indexOf(text) < 0){ nv.push(text); }
    setValue(rid, f.name, nv);
    input.value = "";
    input.focus();
    renderMain();
  }
  input.addEventListener("keydown", function(e){
    if(e.key === "Enter"){ e.preventDefault(); addFromInput(); }
  });
  addBtn.addEventListener("click", addFromInput);
  row.appendChild(input);
  row.appendChild(addBtn);
  box.appendChild(row);
  renderTagBox(f, rid, tagBox);
  return box;
}
function buildLine(f, rid){
  var input = el("input", "line-input");
  input.type = "text";
  input.value = effectiveValue(f, rid);
  input.setAttribute("data-input", f.name);
  input.addEventListener("change", function(){
    setValue(rid, f.name, input.value.trim());
    renderMain();
  });
  return input;
}
function buildChoice(f, rid, multi){
  var box = el("div");
  var list = el("div", "choice-list");
  var counter = null;
  if(multi){
    counter = el("span", "counter");
    box.appendChild(counter);
  }
  var val = effectiveValue(f, rid);
  f.options.forEach(function(opt){
    var label = el("label");
    var input = el("input");
    input.type = multi ? "checkbox" : "radio";
    input.name = "f-" + f.name;
    if(multi){
      input.checked = val.indexOf(opt) >= 0;
    }else{
      input.checked = val === opt;
    }
    input.addEventListener("change", function(){
      if(multi){
        var nv = [];
        var boxes = list.querySelectorAll("input");
        boxes.forEach(function(cb){ if(cb.checked){ nv.push(cb.value); } });
        if(f.max_select && nv.length > f.max_select){
          input.checked = false;
          toast("最多选择 " + f.max_select + " 项");
          updateCounter();
          return;
        }
        setValue(rid, f.name, nv);
      }else{
        setValue(rid, f.name, opt);
      }
      refreshBadge(f, rid);
      if(multi){ updateCounter(); }
    });
    input.value = opt;
    label.appendChild(input);
    label.appendChild(el("span", "", opt));
    list.appendChild(label);
  });
  function updateCounter(){
    if(!counter){ return; }
    var n = effectiveValue(f, rid).length;
    counter.textContent = f.max_select ? ("已选 " + n + " / " + f.max_select) : ("已选 " + n);
    counter.className = "counter" + (f.max_select && n > f.max_select ? " over" : "");
  }
  box.appendChild(list);
  updateCounter();
  return box;
}
function buildJudgment(f, rid){
  var list = el("div", "choice-list");
  var val = effectiveValue(f, rid);
  [["true", "是"], ["false", "否"]].forEach(function(pair){
    var label = el("label");
    var input = el("input");
    input.type = "radio";
    input.name = "f-" + f.name;
    input.value = pair[0];
    input.checked = val === pair[0];
    input.addEventListener("change", function(){
      setValue(rid, f.name, pair[0]);
      refreshBadge(f, rid);
    });
    label.appendChild(input);
    label.appendChild(el("span", "", pair[1]));
    list.appendChild(label);
  });
  return list;
}
function buildField(f, rid){
  var wrap = el("div", "field");
  wrap.appendChild(fieldHead(f, rid));
  if(f.description){ wrap.appendChild(el("div", "field-desc", f.description)); }
  wrap.appendChild(extras(f));
  if(f.widget === "text"){
    wrap.appendChild(buildText(f, rid));
  }else if(f.widget === "line"){
    wrap.appendChild(buildLine(f, rid));
  }else if(f.widget === "choice_single"){
    wrap.appendChild(buildChoice(f, rid, false));
  }else if(f.widget === "choice_multi"){
    wrap.appendChild(buildChoice(f, rid, true));
  }else if(f.widget === "judgment"){
    wrap.appendChild(buildJudgment(f, rid));
  }
  return wrap;
}

/* ---------------- 主区域 ---------------- */
function renderMain(){
  var main = document.getElementById("workarea");
  main.textContent = "";
  var rec = P.records[currentIdx];
  var head = el("div", "rec-head");
  head.appendChild(el("h2", "", rec.title || rec.record_id));
  if(rec.abstract){
    head.appendChild(el("div", "abstract", rec.abstract));
  }
  main.appendChild(head);
  fields.forEach(function(f){
    main.appendChild(buildField(f, rec.record_id));
    refreshBadge(f, rec.record_id);
  });
}

/* ---------------- 示例 chips（事件委托） ---------------- */
document.getElementById("workarea").addEventListener("click", function(e){
  var t = e.target;
  if(!t || !t.getAttribute){ return; }
  var fname = t.getAttribute("data-example");
  if(!fname){ return; }
  var f = null;
  for(var i = 0; i < fields.length; i++){
    if(fields[i].name === fname){ f = fields[i]; break; }
  }
  if(!f){ return; }
  var rid = P.records[currentIdx].record_id;
  var v = t.getAttribute("data-value");
  if(f.widget === "text"){
    var nv = effectiveValue(f, rid).slice();
    if(nv.indexOf(v) < 0){ nv.push(v); }
    setValue(rid, f.name, nv);
    renderMain();
  }else if(f.widget === "choice_multi"){
    var cur = effectiveValue(f, rid).slice();
    var k = cur.indexOf(v);
    if(k >= 0){ cur.splice(k, 1); }
    else if(!f.max_select || cur.length < f.max_select){ cur.push(v); }
    else { toast("最多选择 " + f.max_select + " 项"); return; }
    setValue(rid, f.name, cur);
    renderMain();
  }else if(f.widget === "choice_single" || f.widget === "judgment"){
    setValue(rid, f.name, v);
    renderMain();
  }else if(f.widget === "line"){
    setValue(rid, f.name, v);
    renderMain();
  }
});

/* ---------------- 导出 ---------------- */
function download(name, content, mime){
  var blob = new Blob([content], {type: mime});
  var url = URL.createObjectURL(blob);
  var a = el("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
}
function exportGold(){
  var unfinished = 0;
  var lines = [];
  P.records.forEach(function(rec){
    var row = {record_id: rec.record_id};
    var recUnfinished = false;
    fields.forEach(function(f){
      var v = effectiveValue(f, rec.record_id);
      if(f.is_list){
        row[f.name] = (v || []).map(function(x){ return String(x); });
        if(!row[f.name].length){ recUnfinished = true; }
      }else{
        row[f.name] = v == null ? "" : String(v);
        if(!row[f.name].trim()){ recUnfinished = true; }
      }
    });
    if(recUnfinished){ unfinished++; }
    lines.push(JSON.stringify(row));
  });
  download("gold.jsonl", lines.join("\n") + "\n", "application/jsonl");
  if(unfinished){
    toast("已导出 gold.jsonl；" + unfinished + " / " + P.records.length +
          " 条记录存在未标字段（已按空值导出）");
  }else{
    toast("已导出 gold.jsonl，全部记录标注完整");
  }
}
function exportBackup(){
  var payload = {
    app: "lit-scout-harvester/manual_label",
    content_hash: P.content_hash,
    exported_at: new Date().toISOString(),
    labels: store
  };
  var name = "label_backup_" + P.content_hash + "_" +
             new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".json";
  download(name, JSON.stringify(payload, null, 2), "application/json");
  toast("已导出进度备份 " + name);
}
function resetAll(){
  if(!confirm("确定清空全部标注？此操作不可撤销（可先导出进度备份）。")){ return; }
  store = {};
  saveStore();
  renderAll();
  toast("标注已清空");
}

/* ---------------- 杂项 ---------------- */
var toastTimer = null;
function toast(msg){
  var node = document.getElementById("toast");
  node.textContent = msg;
  node.style.display = "block";
  if(toastTimer){ clearTimeout(toastTimer); }
  toastTimer = setTimeout(function(){ node.style.display = "none"; }, 4000);
}
function renderAll(){
  renderList();
  renderMain();
  updateProgress();
}
document.getElementById("btn-export-gold").addEventListener("click", exportGold);
document.getElementById("btn-export-backup").addEventListener("click", exportBackup);
document.getElementById("btn-reset").addEventListener("click", resetAll);
if(P.adjudication){
  var badge = el("span", "mode", "AI 核对模式");
  document.getElementById("page-title").appendChild(badge);
}
renderAll();
})();
"""

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>人工标注复核页</title>
<style>{css}</style>
</head>
<body>
<header>
  <div class="title" id="page-title">人工标注复核页</div>
  <div class="progress-wrap">
    <span id="progress-text"></span>
    <div class="bar"><div id="progress-bar"></div></div>
  </div>
  <div class="actions">
    <button id="btn-export-gold" class="primary">导出 gold JSONL</button>
    <button id="btn-export-backup">导出进度备份</button>
    <button id="btn-reset">清空标注</button>
  </div>
</header>
<div class="layout">
  <aside>
    <div class="legend">
      <span><i style="background:var(--ok)"></i>完成</span>
      <span><i style="background:var(--partial)"></i>部分完成</span>
      <span><i style="background:var(--todo)"></i>未标</span>
    </div>
    <div id="record-list"></div>
  </aside>
  <main id="workarea"></main>
</div>
<div id="toast"></div>
<script type="application/json" id="lsh-payload">{payload}</script>
<script>{js}</script>
</body>
</html>
"""


def render_label_html(payload: Dict[str, Any]) -> str:
    """把 ``build_label_payload`` 的载荷渲染成单文件 HTML 字符串。

    零外部引用（无外部 script/link/img，离线双击可用）；标注态按
    ``content_hash`` 键存 localStorage，刷新续标。
    """
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE_TEMPLATE.format(css=_CSS, payload=payload_json, js=_JS)

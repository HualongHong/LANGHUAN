"""模型运营商设置页 —— Streamlit 单文件 App。

启动（技能根目录）：
    streamlit run scripts/settings_app.py

本页集中管理两件事：
1. ``config/providers.json`` —— 运营商入口与「标注模型 / 选项决策模型」角色槽；
2. 中央 env 文件（默认 ``~/.agents/skills/langhuan.env``）—— API key 经
   环境变量名引用，password 框只写不读。

所有写操作都是 ``lit_scout_harvester.core.provider_registry`` 里的纯函数；
本文件只做 UI 组装，顶层不执行任何 st 调用（可安全 import 供冒烟测试）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from lit_scout_harvester.core import provider_registry as reg  # noqa: E402

# 尽力预载技能根 .env 到 os.environ（有 pydantic 的 venv 才做得到；失败不影响
# key 状态灯——后者还会直接查 env 文件的存在性）。
try:  # pragma: no cover - 依赖宿主 venv 是否装有 pydantic
    from lit_scout_harvester.core import config_loader  # noqa: F401
except Exception:  # noqa: BLE001
    pass

REGISTRY_PATH = reg.skill_root() / "config" / "providers.json"
SS_KEY = "lsh_provider_registry"


# ---------------------------------------------------------------------------
# 纯 UI 辅助（不含写操作）
# ---------------------------------------------------------------------------


def _entries_to_rows(entries: List[reg.ProviderEntry]) -> List[dict]:
    return [
        {
            "id": e.id,
            "operator": e.operator,
            "type": e.type,
            "base_url": e.base_url,
            "api_key_env": e.api_key_env,
            "models": ", ".join(e.models),
            "enabled": e.enabled,
            "note": e.note,
        }
        for e in entries
    ]


def _row_to_entry(row: dict, old: Optional[reg.ProviderEntry]) -> reg.ProviderEntry:
    models = [m.strip() for m in str(row.get("models", "")).split(",") if m.strip()]
    return reg.ProviderEntry(
        id=str(row.get("id", "")).strip(),
        operator=str(row.get("operator", "")) or (old.operator if old else ""),
        type=(old.type if old else reg.TYPE_OPENAI_COMPATIBLE),
        base_url=str(row.get("base_url", "")).strip(),
        api_key_env=str(row.get("api_key_env", "")).strip(),
        ssh_host=(old.ssh_host if old else ""),
        ssh_port=(old.ssh_port if old else 22),
        ssh_user=(old.ssh_user if old else ""),
        ssh_key_path=(old.ssh_key_path if old else ""),
        remote_host=(old.remote_host if old else ""),
        remote_port=(old.remote_port if old else 0),
        local_port=(old.local_port if old else 0),
        models=models,
        enabled=bool(row.get("enabled", False)),
        supports_logprobs=old.supports_logprobs if old else False,
        reserved=False,
        note=str(row.get("note", "")),
    )


def _rows_to_entries(rows: List[dict], registry: reg.ProviderRegistry) -> List[reg.ProviderEntry]:
    """编辑器行合并回 registry，保持原有顺序（reserved 原样保留、不挪位）。

    type / supports_logprobs / operator 不在编辑范围内，从既有 entry 继承。
    """
    by_row = {str(r.get("id", "")).strip(): r for r in rows if str(r.get("id", "")).strip()}
    existing = {e.id: e for e in registry.providers}
    entries: List[reg.ProviderEntry] = []
    for old in registry.providers:
        if old.reserved:
            entries.append(old)  # 预留条目不进编辑器，原样保留在原位置
            continue
        row = by_row.pop(old.id, None)
        if row is None:
            continue  # 该行已从编辑器移除
        entries.append(_row_to_entry(row, old))
    for rid, row in by_row.items():  # 编辑器中多出的行（异常路径，保底）
        entries.append(_row_to_entry(row, existing.get(rid)))
    return entries


def _key_configured(env_name: str, env_path: Path) -> bool:
    """key 状态灯：只查存在性，永不读取/显示值。"""
    if not env_name:
        return False
    return env_name in os.environ or reg.env_file_has_key(env_path, env_name)


def _enabled_options(registry: reg.ProviderRegistry) -> List[reg.ProviderEntry]:
    return [e for e in registry.providers if e.enabled and not e.reserved]


# ---------------------------------------------------------------------------
# 连接测试（真实联网调用；开发期不运行）
# ---------------------------------------------------------------------------


async def _ping_provider(entry: reg.ProviderEntry, role: str) -> str:
    from lit_scout_harvester.core.llm_providers import get_provider

    cfg = reg.build_provider_config(entry, role=role)
    provider = get_provider(
        cfg["provider"],
        model=cfg["model"],
        api_key=cfg["api_key"] or "",
        base_url=cfg["base_url"],
        timeout=30,
    )
    return await provider.chat(
        messages=[{"role": "user", "content": "用一句话回答：1+1等于几？"}],
        temperature=0.0,
        max_tokens=32,
    )


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="LSH 模型运营商设置", page_icon="⚙️", layout="wide")
    st.title("⚙️ 模型运营商设置")
    st.caption(
        "本页管理 config/providers.json 与中央 env 文件（key 只经环境变量名引用，不落盘到仓库）；"
        "不设 key 可改用 agent_bridge 零 key 模式。"
    )

    if SS_KEY not in st.session_state:
        st.session_state[SS_KEY] = reg.load_registry(REGISTRY_PATH)
    registry: reg.ProviderRegistry = st.session_state[SS_KEY]

    user_env_path = st.sidebar.text_input(
        "自定义 env 路径（兜底）",
        value="",
        help="中央 langhuan.env 与技能根 .env 都不存在时，key 将写入此路径。",
    )
    env_path = reg.detect_env_file(user_path=user_env_path or None)
    st.sidebar.markdown(f"**当前 env 文件** `{env_path}`")

    # ------------------------------------------------------------------ #
    # 1. 运营商入口
    # ------------------------------------------------------------------ #
    st.header("1️⃣ 运营商入口")
    reserved_entries = [e for e in registry.providers if e.reserved]
    editable_entries = [e for e in registry.providers if not e.reserved]

    if reserved_entries:
        st.markdown("**预留入口（不可访问，仅保留）**")
        reserved_rows = [
            {**_entries_to_rows([e])[0], "状态": "🔒 预留·不可访问"} for e in reserved_entries
        ]
        st.dataframe(reserved_rows, use_container_width=True, hide_index=True)

    st.markdown("**可用入口（预置条目只能禁用，不可删除）**")
    edited = st.data_editor(
        _entries_to_rows(editable_entries),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "id": st.column_config.TextColumn("id", disabled=True),
            "operator": st.column_config.TextColumn("运营商", disabled=True),
            "type": st.column_config.TextColumn("类型", disabled=True),
            "base_url": st.column_config.TextColumn("base_url"),
            "api_key_env": st.column_config.TextColumn("api_key_env（环境变量名）"),
            "models": st.column_config.TextColumn("models（逗号分隔）"),
            "enabled": st.column_config.CheckboxColumn("启用"),
            "note": st.column_config.TextColumn("备注", disabled=True),
        },
        key="provider_editor",
    )

    add_col, del_col = st.columns(2)
    missing_presets = [pid for pid in reg.PRESET_IDS if registry.get(pid) is None]
    with add_col:
        if missing_presets:
            choice = st.selectbox("从预置添加", ["（选择）"] + list(missing_presets))
            if st.button("➕ 添加预置条目", disabled=choice == "（选择）"):
                registry.providers.append(reg.preset_entry(choice))
                st.session_state[SS_KEY] = registry
                st.rerun()
        else:
            st.caption("全部预置条目已在列表中。")
    with del_col:
        custom_ids = [e.id for e in registry.providers if e.id not in reg.PRESET_IDS]
        if custom_ids:
            target = st.selectbox("删除自定义条目（预置不可删）", ["（选择）"] + custom_ids)
            if st.button("🗑️ 删除", disabled=target == "（选择）"):
                registry.providers = [e for e in registry.providers if e.id != target]
                if registry.annotation_provider == target:
                    registry.annotation_provider = None
                if registry.decision_provider == target:
                    registry.decision_provider = None
                st.session_state[SS_KEY] = registry
                st.rerun()
        else:
            st.caption("暂无自定义条目可删除。")

    # ssh_tunnel 条目的 SSH 连接参数（不进主表格，单独展开编辑；model_deployment
    # 条目无附加字段，base_url / api_key_env 在主表格中正常编辑即可）。
    ssh_entries = [e for e in editable_entries if e.type == reg.TYPE_SSH_TUNNEL]
    if ssh_entries:
        st.markdown("**SSH 隧道参数（type = ssh_tunnel）**")
        st.caption(
            "隧道把远端服务器上的 OpenAI 兼容端点转发到本地：base_url 应指向 "
            "http://127.0.0.1:<local_port>/v1。隧道进程用 "
            "`python -B scripts/model_tunnel.py start|stop|status` 管理（providers.json 中 "
            "已启用且 type=ssh_tunnel 的条目）。"
        )
    for entry in ssh_entries:
        with st.expander(f"🔐 {entry.operator}（{entry.id}）"):
            c1, c2, c3 = st.columns(3)
            with c1:
                entry.ssh_host = st.text_input(
                    "ssh_host", value=entry.ssh_host, key=f"ssh_host_{entry.id}",
                    help="SSH 服务器地址（域名或 IP）。",
                )
                entry.ssh_user = st.text_input(
                    "ssh_user", value=entry.ssh_user, key=f"ssh_user_{entry.id}",
                    help="SSH 登录用户。",
                )
                entry.ssh_key_path = st.text_input(
                    "ssh_key_path", value=entry.ssh_key_path, key=f"ssh_key_{entry.id}",
                    help="私钥路径，留空走默认密钥 / ssh-agent。",
                )
            with c2:
                entry.ssh_port = int(
                    st.number_input(
                        "ssh_port", value=int(entry.ssh_port or 22), min_value=1, max_value=65535,
                        step=1, key=f"ssh_port_{entry.id}",
                    )
                )
                entry.remote_host = st.text_input(
                    "remote_host", value=entry.remote_host, key=f"remote_host_{entry.id}",
                    help="转发目标地址：远端侧 OpenAI 端点所在 host（隧道服务器视角）。",
                )
                entry.remote_port = int(
                    st.number_input(
                        "remote_port", value=int(entry.remote_port or 0), min_value=0, max_value=65535,
                        step=1, key=f"remote_port_{entry.id}",
                        help="转发目标端口；0 = 未填写（启用前必填）。",
                    )
                )
            with c3:
                entry.local_port = int(
                    st.number_input(
                        "local_port", value=int(entry.local_port or 0), min_value=0, max_value=65535,
                        step=1, key=f"local_port_{entry.id}",
                        help="本地监听端口；0 = 未填写（启用前必填）。",
                    )
                )
            st.caption(
                f"等价命令：ssh -N -L 127.0.0.1:{entry.local_port or '<local_port>'}:"
                f"{entry.remote_host or '<remote_host>'}:{entry.remote_port or '<remote_port>'} "
                f"{entry.ssh_user or '<ssh_user>'}@{entry.ssh_host or '<ssh_host>'} -p {entry.ssh_port}"
                + (f" -i {entry.ssh_key_path}" if entry.ssh_key_path else "")
            )

    # ------------------------------------------------------------------ #
    # 2. 角色分配
    # ------------------------------------------------------------------ #
    st.header("2️⃣ 角色分配")
    enabled = _enabled_options(registry)
    if not enabled:
        st.warning("当前没有已启用的可用入口，请先在上方勾选「启用」。")
    id_labels = {f"{e.id} — {e.operator}": e.id for e in enabled}

    def _role_select(label: str, current: Optional[str], key: str) -> Optional[str]:
        options = list(id_labels.keys())
        default_idx = 0
        for i, label_full in enumerate(options):
            if id_labels[label_full] == current:
                default_idx = i
                break
        picked = st.selectbox(label, options, index=default_idx, key=key) if options else None
        return id_labels[picked] if picked else None

    c1, c2 = st.columns(2)
    with c1:
        annotation_id = _role_select("标注模型（阶段 A/B 生成式）", registry.annotation_provider, "role_ann")
    with c2:
        decision_id = _role_select("选项决策模型（阶段 B 选择）", registry.decision_provider, "role_dec")

    decision_entry = registry.get(decision_id) if decision_id else None
    if decision_entry and not decision_entry.supports_logprobs:
        st.error(
            f"⚠️ 决策模型 '{decision_entry.id}' 未声明支持 logprobs —— 阶段 B 需要概率排序输出，"
            "请换用支持 logprobs 的入口（如 DeepSeek、上海超算·类JEV决策模型）。"
        )

    st.markdown("**当前解析结果**")
    for role, pid in ((reg.ROLE_ANNOTATION, annotation_id), (reg.ROLE_DECISION, decision_id)):
        entry = registry.get(pid) if pid else None
        if entry is None:
            st.markdown(f"- `{role}`：未配置")
            continue
        cfg = reg.build_provider_config(entry, role=role)
        key_ok = _key_configured(entry.api_key_env, env_path)
        light = "✅ 已配置" if key_ok else "❌ 未配置"
        st.markdown(
            f"- `{role}` → **{entry.operator}**（`{entry.id}`）｜模型 `{cfg['model'] or '（待填）'}`｜"
            f"provider `{cfg['provider']}`｜key `{entry.api_key_env}` {light}"
        )

    # ------------------------------------------------------------------ #
    # 3. API key 管理
    # ------------------------------------------------------------------ #
    st.header("3️⃣ API key 管理")
    st.caption(
        f"写入目标：`{env_path}`（读-改-写，保留其它行）。密码框默认留空表示不改动；"
        "只查 key 存在性，永不回显 key 值。"
    )
    for entry in registry.providers:
        kcol1, kcol2 = st.columns([3, 1])
        with kcol1:
            value = st.text_input(
                f"{entry.operator}（{entry.api_key_env or '未设置环境变量名'}）",
                type="password",
                key=f"key_{entry.id}",
                disabled=not entry.api_key_env,
            )
        with kcol2:
            configured = _key_configured(entry.api_key_env, env_path)
            st.markdown(f"{'✅' if configured else '❌'} {'已配置' if configured else '未配置'}")
            if st.button("保存 key", key=f"save_key_{entry.id}", disabled=not (value and entry.api_key_env)):
                reg.upsert_env_value(env_path, entry.api_key_env, value.strip())
                st.success(f"已写入 {env_path.name}：{entry.api_key_env}")
                st.rerun()

    # ------------------------------------------------------------------ #
    # 保存 + 校验 + 连接测试
    # ------------------------------------------------------------------ #
    st.header("💾 保存")
    errors = reg.validate(
        reg.ProviderRegistry(
            providers=_rows_to_entries(edited, registry),
            annotation_provider=annotation_id,
            decision_provider=decision_id,
        )
    )
    if errors:
        for err in errors:
            st.error(f"校验失败：{err}")

    save_col, test_col = st.columns(2)
    with save_col:
        if st.button("保存 providers.json", type="primary", disabled=bool(errors)):
            registry.providers = _rows_to_entries(edited, registry)
            registry.annotation_provider = annotation_id
            registry.decision_provider = decision_id
            reg.save_registry(REGISTRY_PATH, registry)
            st.session_state[SS_KEY] = registry
            st.success(f"已保存 {REGISTRY_PATH.name}（{len(registry.providers)} 个条目）。")
            st.rerun()
    with test_col:
        testable = [e for e in enabled if e.api_key_env and _key_configured(e.api_key_env, env_path)]
        if testable:
            test_pick = st.selectbox("连接测试（真实联网调用）", [f"{e.id} — {e.operator}" for e in testable])
            if st.button("🔌 发起最小 chat 请求"):
                entry = next(e for e in testable if f"{e.id} — {e.operator}" == test_pick)
                role = reg.ROLE_DECISION if entry.type == reg.TYPE_OPENAI_COMPATIBLE_DECISION else reg.ROLE_ANNOTATION
                try:
                    reply = asyncio.run(_ping_provider(entry, role))
                    st.success(f"连接成功：{reply[:120]}")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"连接失败：{exc}")
        else:
            st.caption("连接测试需要已启用且已配置 key 的入口。")


if __name__ == "__main__":
    main()

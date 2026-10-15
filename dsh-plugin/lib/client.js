/*
 * @langhuan/dsh-plugin —— 浏览器半侧（DSH 客户端模块系统 lazy-CJS factory）。
 *
 * 契约（对照 dsh-client-ui-settings-plugins/lib/client.js 与官方
 * packages/client/{modules,ui-slots,web} 源码确认，DSH 0.1.5-rc.3）：
 * - 入口格式：window.__ModuleLoader__.load({ id: <裸包名>, factory(require) })；
 *   factory 内 require 只解析 PLATFORM_MODULES 基座（react、
 *   @deepseek-ai/dsh-client-store 等）或 dsh.client.external 声明的 row；
 * - 模块体保持惰性：副作用（本插件无 CSS）须在 factory 闭包内；
 * - 客户端插件导出 { apply, inject }（cordis 浏览器侧插件形态）；
 * - 卡片注册进 settings.plugin.item（keyed slot，options.key = settings
 *   命名空间），由 ui-settings-plugins 的「插件配置」页按
 *   「Host 服务的命名空间 ∩ 卡片认领」分发渲染；
 * - inject() 返回 { hooks: {<name>: observableStore}, ...actions }：
 *   hooks 成员变成组件的 use<Name> 选择器钩子，其余成员平铺为 props；
 * - 写入经 ctx.settingsScope.bind({namespace}).mutate(ops, expectedRevision?)，
 *   ops 为 path-addressed {op:'set'|'unset'}，服务端校验拒绝时草稿保留。
 *
 * 本插件只 require 基座内的 react，故 dsh.client 无需 external 字段。
 */
window.__ModuleLoader__.load({
  id: '@langhuan/dsh-plugin',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
    let react = require('react');

    //#region langhuan client constants
    /** Locale dictionary namespace（注册进 ctx.locale）。 */
    const NS = 'langhuan';
    /** Host 侧 settings 命名空间（与服务端 installSection 的 ns 一致）。 */
    const SETTINGS_NS = 'langhuan';
    /** 客户端插件依赖的浏览器侧服务。 */
    const inject = ['slots', 'locale', 'settingsScope'];

    const BOOL_FIELDS = new Set(['enabled', 'supportsLogprobs', 'reserved']);
    /** 可编辑字段（apiKeyEnv/type/reserved 只读展示）。 */
    const EDITABLE_FIELDS = ['operator', 'baseUrl', 'models', 'enabled', 'supportsLogprobs', 'note'];
    /** 只读展示字段。 */
    const READONLY_FIELDS = ['type', 'apiKeyEnv'];

    const zh = {
      cardTitle: 'LangHuan 模型运营商',
      cardDescription: 'LangHuan 技能群的模型入口、标注/决策角色与密钥环境变量名',
      unsaved: '未保存',
      readOnly: '当前部署不可写此配置。',
      save: '保存',
      discard: '放弃',
      saving: '保存中…',
      saveFailed: '保存被拒绝：配置校验未通过或已在其他位置变更，草稿已保留。',
      roleInvalid: '角色选择无效',
      sectionProviders: '运营商入口',
      sectionRoles: '角色分配',
      sectionSkillsDir: '技能目录',
      skillsDirHint: 'providers.json 写到该目录下 langhuan-lit-scout-harvester/config/。',
      colEnabled: '启用',
      colOperator: '运营商（id / 显示名）',
      colType: '类型',
      colBaseUrl: 'Base URL',
      colApiKeyEnv: '密钥环境变量名',
      colModels: '模型（逗号分隔）',
      colLogprobs: 'logprobs',
      colNote: '备注',
      reservedLock: '预留·不可访问（锁定）',
      roleAnnotation: '标注角色（阶段 A 生成式标注）',
      roleDecision: '决策角色（阶段 B 选项内选择，要求 logprobs）',
      roleNone: '（不配置）',
      roleDisabled: '未启用',
      roleReserved: '预留入口，禁止选用',
      roleNoLogprobs: '不支持 logprobs',
      invalidEnabled: '已启用但 Base URL / 密钥环境变量名为空',
      tunnelBadge: '隧道',
      tunnelSummaryMissing: 'SSH 参数待填',
      apiKeyEnvHint: '只读：key 的值填在 ~/.agents/skills/langhuan.env（单一事实来源，硬链接分发给各 langhuan-* 技能）。',
      noProviders: '（providers 为空）'
    };
    const en = {
      cardTitle: 'LangHuan Model Providers',
      cardDescription: 'Model routes, annotation/decision roles and API-key env-var names for the LangHuan skills',
      unsaved: 'unsaved',
      readOnly: 'This deployment does not allow editing this configuration.',
      save: 'Save',
      discard: 'Discard',
      saving: 'Saving…',
      saveFailed: 'Save refused: validation failed or the setting changed elsewhere; your drafts are kept.',
      roleInvalid: 'Invalid role selection',
      sectionProviders: 'Provider routes',
      sectionRoles: 'Role assignment',
      sectionSkillsDir: 'Skills directory',
      skillsDirHint: 'providers.json is written under <dir>/langhuan-lit-scout-harvester/config/.',
      colEnabled: 'Enabled',
      colOperator: 'Operator (id / display name)',
      colType: 'Type',
      colBaseUrl: 'Base URL',
      colApiKeyEnv: 'API-key env var',
      colModels: 'Models (comma separated)',
      colLogprobs: 'logprobs',
      colNote: 'Note',
      reservedLock: 'Reserved · unavailable (locked)',
      roleAnnotation: 'Annotation role (stage A generative labeling)',
      roleDecision: 'Decision role (stage B in-option select; requires logprobs)',
      roleNone: '(not configured)',
      roleDisabled: 'not enabled',
      roleReserved: 'reserved route, selection disabled',
      roleNoLogprobs: 'logprobs unsupported',
      invalidEnabled: 'enabled but Base URL / API-key env var is empty',
      tunnelBadge: 'tunnel',
      tunnelSummaryMissing: 'SSH params to fill',
      apiKeyEnvHint: 'Read-only: put the key value in ~/.agents/skills/langhuan.env (single source of truth, hard-linked into every langhuan-* skill).',
      noProviders: '(no providers)'
    };
    //#endregion

    //#region observable store（ObservableSnapshot 最小形态）
    function createStore(initial) {
      let snapshot = initial;
      const listeners = new Set();
      return {
        getSnapshot: () => snapshot,
        subscribe: (fn) => {
          listeners.add(fn);
          return () => listeners.delete(fn);
        },
        set: (next) => {
          snapshot = next;
          for (const fn of [...listeners]) fn();
        }
      };
    }
    //#endregion

    //#region staging / validation core
    /** staged 以字符串键存放：'providers.<id>.<field>' 或顶层字段名。 */
    const pathOf = (key) => (key.includes('.') ? key.split('.') : [key]);

    /** 与 Python provider_registry.validate / 服务端 validateConfig 同语义。 */
    function validateResolved(config) {
      const errors = [];
      const providers = config.providers || {};
      for (const [id, entry] of Object.entries(providers)) {
        if (entry.enabled) {
          if (!entry.baseUrl) errors.push({ target: 'providers.' + id, message: 'empty baseUrl' });
          if (!entry.apiKeyEnv) errors.push({ target: 'providers.' + id, message: 'empty apiKeyEnv' });
          if (entry.type === 'ssh_tunnel') {
            if (!entry.sshHost) errors.push({ target: 'providers.' + id, message: 'empty sshHost' });
            if (!entry.sshUser) errors.push({ target: 'providers.' + id, message: 'empty sshUser' });
            if (!entry.localPort) errors.push({ target: 'providers.' + id, message: 'empty localPort' });
          }
        }
      }
      const checkRole = (role, slot, extra) => {
        if (!slot) return;
        const entry = providers[slot];
        if (!entry) { errors.push({ target: role, message: 'missing' }); return; }
        if (!entry.enabled) errors.push({ target: role, message: 'disabled' });
        if (entry.reserved) errors.push({ target: role, message: 'reserved' });
        if (extra && !entry.supportsLogprobs) errors.push({ target: role, message: 'no-logprobs' });
      };
      checkRole('annotationProvider', config.annotationProvider);
      checkRole('decisionProvider', config.decisionProvider, true);
      return errors;
    }

    /** 角色下拉可选项（含禁用原因），基于叠加草稿后的投影。 */
    function roleOptions(config, t) {
      const providers = config.providers || {};
      const build = (role) => Object.entries(providers).map(([id, entry]) => {
        let reason = '';
        if (!entry.enabled) reason = t('roleDisabled');
        else if (entry.reserved) reason = t('roleReserved');
        else if (role === 'decision' && !entry.supportsLogprobs) reason = t('roleNoLogprobs');
        return { value: id, label: entry.operator || id, disabled: reason !== '', reason };
      });
      return { annotation: build('annotation'), decision: build('decision') };
    }

    /** ssh_tunnel 行摘要：ssh_user@ssh_host:port → remote:port（缺参数时提示待填）。 */
    function tunnelSummaryText(tunnel, t) {
      if (!tunnel.sshHost || !tunnel.sshUser) return t('tunnelSummaryMissing');
      const remote = tunnel.remoteHost ? tunnel.remoteHost + ':' + (tunnel.remotePort || '?') : '?';
      const local = tunnel.localPort ? ':' + tunnel.localPort : ':?';
      return tunnel.sshUser + '@' + tunnel.sshHost + ':' + tunnel.sshPort + ' → ' + remote + '（' + local + '）';
    }
    //#endregion

    //#region card controller
    /**
     * 卡片控制器：staging + 投影 + 保存（单条原子 mutate）。
     * 对照 ui-settings-plugins 的 CardForm 语义：Host 是值的唯一权威，
     * 保存后经回读确认落地，被拒时保留草稿。
     */
    class LanghuanCardController {
      constructor(scope, t) {
        this.scope = scope;
        this.t = t;
        this.staged = new Map();
        this.saving = false;
        this.failed = false;
        this.store = createStore(this.project());
        this.unsubscribe = scope.subscribe(() => this.publish());
      }
      dispose() { this.unsubscribe?.(); }

      snapshot() { return this.scope.getSnapshot(); }
      value() { return this.snapshot().value; }
      base() { return this.snapshot().base; }

      baseValue(path) {
        let node = this.base();
        for (const seg of path) node = node?.[seg];
        return node;
      }
      formatField(field, raw) {
        return field === 'models' ? (raw || []).join(', ') : String(raw ?? '');
      }

      /** 草稿叠加到 Host 解析值上的投影；clear 草稿等价于 unset（回落 base）。 */
      projected() {
        const baseValue = this.value();
        if (!baseValue) return baseValue;
        const next = { ...baseValue, providers: { ...baseValue.providers } };
        for (const [key, staged] of this.staged) {
          const path = pathOf(key);
          if (path[0] === 'providers' && path.length === 3) {
            const entry = { ...(next.providers[path[1]] || {}) };
            if (staged.clear) delete entry[path[2]];
            else entry[path[2]] = staged.value;
            next.providers[path[1]] = entry;
          } else if (path.length === 1) {
            if (staged.clear) delete next[path[0]];
            else next[path[0]] = staged.value;
          }
        }
        return next;
      }

      project() {
        const snap = this.snapshot();
        const projected = this.projected();
        const ready = snap.status === 'ready' && projected != null;
        const errors = ready ? validateResolved(projected) : [];
        const stagedKeys = new Set(this.staged.keys());
        const invalid = [...this.staged.values()].some((s) => s.invalid)
          || errors.some((e) => stagedKeys.has(e.target));
        const providers = {};
        if (ready) {
          for (const [id, entry] of Object.entries(projected.providers || {})) {
            const row = { id, reserved: !!entry.reserved, fields: {} };
            if (entry.type === 'ssh_tunnel') {
              row.tunnel = {
                sshHost: entry.sshHost || '',
                sshPort: entry.sshPort || 22,
                sshUser: entry.sshUser || '',
                remoteHost: entry.remoteHost || '',
                remotePort: entry.remotePort || 0,
                localPort: entry.localPort || 0,
              };
            }
            for (const field of EDITABLE_FIELDS) {
              const key = 'providers.' + id + '.' + field;
              const staged = this.staged.get(key);
              if (staged?.clear) {
                row.fields[field] = {
                  text: this.formatField(field, this.baseValue(pathOf(key))),
                  staged: true, invalid: false, overridden: false
                };
              } else if (staged) {
                row.fields[field] = { text: staged.text, staged: true, invalid: staged.invalid, overridden: !staged.invalid };
              } else {
                row.fields[field] = {
                  text: this.formatField(field, entry[field]),
                  staged: false, invalid: false, overridden: this.isOverridden(key)
                };
              }
            }
            for (const field of READONLY_FIELDS) {
              row.fields[field] = { text: this.formatField(field, entry[field]), staged: false, invalid: false, overridden: false };
            }
            providers[id] = row;
          }
        }
        const roleField = (name) => {
          const staged = this.staged.get(name);
          const invalidRole = staged?.invalid === true || errors.some((e) => e.target === name);
          return {
            key: name,
            value: staged && !staged.clear ? staged.value : (projected?.[name] ?? ''),
            staged: staged !== undefined && !staged.invalid,
            invalid: invalidRole
          };
        };
        return {
          available: snap.status === 'ready',
          writable: snap.writable === true,
          dirty: this.staged.size > 0,
          invalid,
          saving: this.saving,
          failed: this.failed,
          providers,
          annotation: roleField('annotationProvider'),
          decision: roleField('decisionProvider'),
          roleOptions: ready ? roleOptions(projected, this.t) : { annotation: [], decision: [] },
          skillsDir: projected?.skillsDir ?? ''
        };
      }

      isOverridden(key) {
        const path = pathOf(key);
        let node = this.snapshot().user;
        for (const seg of path) {
          if (!node || typeof node !== 'object') return false;
          node = node[seg];
        }
        return node !== undefined;
      }

      publish() { this.store.set(this.project()); }

      /** stage 一次编辑；parse 失败立即标 invalid（保存会被拒）。 */
      edit(key, raw, parse) {
        const parsed = parse ? parse(raw) : { ok: true, value: raw, text: String(raw) };
        if (parsed.ok) this.staged.set(key, { value: parsed.value, text: parsed.text, invalid: false });
        else this.staged.set(key, { value: undefined, text: parsed.text, invalid: true });
        this.failed = false;
        this.publish();
      }

      /** 复位该字段：保存时发 unset，回落实处后回落到组合默认值。 */
      resetField(key) {
        this.staged.set(key, { clear: true, text: '', invalid: false });
        this.failed = false;
        this.publish();
      }

      discard() {
        if (this.staged.size === 0 && !this.failed) return;
        this.staged.clear();
        this.failed = false;
        this.publish();
      }

      async save() {
        if (this.saving || this.staged.size === 0) return;
        if (this.project().invalid) return;
        const ops = [];
        for (const [key, staged] of this.staged) {
          if (staged.invalid) return;
          ops.push(staged.clear ? { op: 'unset', path: pathOf(key) } : { op: 'set', path: pathOf(key), value: staged.value });
        }
        this.saving = true;
        this.failed = false;
        this.publish();
        await this.scope.mutate(ops);
        // Host 是唯一权威：回读确认（mutate 被拒时静默落定，草稿保留）。
        const landed = [...this.staged.keys()].every((key) => {
          const path = pathOf(key);
          const staged = this.staged.get(key);
          let node = this.value();
          for (const seg of path) node = node?.[seg];
          if (staged.clear) return node === undefined || JSON.stringify(node) === JSON.stringify(this.baseValue(path));
          return JSON.stringify(node) === JSON.stringify(staged.value);
        });
        this.saving = false;
        if (landed) this.staged.clear();
        this.failed = !landed;
        this.publish();
      }

      inject() {
        const textParse = (raw) => ({ ok: true, value: String(raw), text: String(raw) });
        const modelsParse = (raw) => {
          const text = String(raw);
          return { ok: true, value: text.split(',').map((s) => s.trim()).filter(Boolean), text };
        };
        const boolParse = (raw) => (typeof raw === 'boolean'
          ? { ok: true, value: raw, text: raw }
          : { ok: false, value: undefined, text: raw });
        return {
          hooks: { langhuanCard: this.store },
          edit: (key, raw) => {
            const field = key.split('.').pop();
            if (field === 'models') this.edit(key, raw, modelsParse);
            else if (BOOL_FIELDS.has(field)) this.edit(key, raw, boolParse);
            else this.edit(key, raw, textParse);
          },
          resetField: (key) => this.resetField(key),
          save: () => this.save(),
          discard: () => this.discard()
        };
      }
    }
    //#endregion

    //#region card component（createElement，内联样式 + DSW 主题变量）
    const cssVar = (name, fallback) => 'var(' + name + ',' + fallback + ')';
    const S = {
      card: { listStyle: 'none', border: '0.5px solid ' + cssVar('--dsw-alias-border-l4', '#3a3a3a'), borderRadius: '10px', background: cssVar('--dsw-alias-bg-layer-2', '#232323'), margin: '8px 0' },
      header: { display: 'flex', alignItems: 'center', gap: '8px', width: '100%', padding: '12px 14px', background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', font: 'inherit', textAlign: 'left' },
      title: { fontSize: '13px', fontWeight: 600, color: cssVar('--dsw-alias-label-primary', '#eee') },
      desc: { fontSize: '12px', color: cssVar('--dsw-alias-label-tertiary', '#999'), marginTop: '2px' },
      pending: { fontSize: '12px', color: cssVar('--dsw-alias-label-secondary', '#aaa'), border: '0.5px solid ' + cssVar('--dsw-alias-border-l4', '#444'), borderRadius: '999px', padding: '1px 8px' },
      body: { padding: '4px 14px 14px', display: 'flex', flexDirection: 'column', gap: '12px' },
      sectionTitle: { fontSize: '12px', fontWeight: 600, color: cssVar('--dsw-alias-label-secondary', '#aaa'), margin: '6px 0 2px' },
      hint: { fontSize: '12px', color: cssVar('--dsw-alias-label-tertiary', '#888'), margin: '2px 0 0' },
      error: { fontSize: '12px', color: cssVar('--dsw-alias-label-error', '#e5534b'), margin: '2px 0 0' },
      table: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' },
      th: { textAlign: 'left', fontWeight: 500, color: cssVar('--dsw-alias-label-tertiary', '#888'), padding: '4px 6px', borderBottom: '0.5px solid ' + cssVar('--dsw-alias-border-l2', '#3a3a3a'), whiteSpace: 'nowrap' },
      td: { padding: '4px 6px', borderBottom: '0.5px solid ' + cssVar('--dsw-alias-border-l2', '#333'), verticalAlign: 'top' },
      input: { width: '100%', boxSizing: 'border-box', height: '28px', font: 'inherit', fontSize: '12px', color: cssVar('--dsw-alias-label-primary', '#eee'), background: cssVar('--dsw-alias-bg-layer-3', '#2b2b2b'), border: '0.5px solid ' + cssVar('--dsw-alias-border-l4', '#444'), borderRadius: '6px', padding: '0 8px' },
      inputInvalid: { borderColor: cssVar('--dsw-alias-label-error', '#e5534b') },
      select: { height: '28px', font: 'inherit', fontSize: '12px', color: cssVar('--dsw-alias-label-primary', '#eee'), background: cssVar('--dsw-alias-bg-layer-3', '#2b2b2b'), border: '0.5px solid ' + cssVar('--dsw-alias-border-l4', '#444'), borderRadius: '6px', padding: '0 6px', maxWidth: '320px' },
      footer: { display: 'flex', gap: '8px', justifyContent: 'flex-end' },
      button: { font: 'inherit', fontSize: '12px', height: '28px', padding: '0 12px', borderRadius: '6px', cursor: 'pointer' },
      buttonPrimary: { background: cssVar('--dsw-alias-brand-primary', '#4d6bfe'), color: '#fff', border: 'none' },
      buttonGhost: { background: 'none', color: cssVar('--dsw-alias-label-secondary', '#aaa'), border: '0.5px solid ' + cssVar('--dsw-alias-border-l4', '#444') },
      lockTag: { fontSize: '11px', color: cssVar('--dsw-alias-label-tertiary', '#888'), border: '0.5px dashed ' + cssVar('--dsw-alias-border-l4', '#444'), borderRadius: '999px', padding: '0 6px', whiteSpace: 'nowrap', display: 'inline-block', marginTop: '2px' },
      tunnelBadge: { fontSize: '11px', color: cssVar('--dsw-alias-label-tertiary', '#888'), border: '0.5px dashed ' + cssVar('--dsw-alias-border-l4', '#444'), borderRadius: '999px', padding: '0 6px', whiteSpace: 'nowrap', display: 'inline-block' },
      roleRow: { display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' },
      roleLabel: { fontSize: '12px', color: cssVar('--dsw-alias-label-primary', '#ddd'), minWidth: '220px' }
    };
    const inputStyle = (invalid, readOnly) => ({
      ...S.input,
      ...(invalid ? S.inputInvalid : {}),
      ...(readOnly ? { opacity: 0.75 } : {})
    });

    function FieldInput(props) {
      const { field, disabled, readOnly, invalid, onEdit } = props;
      return react.createElement('div', null,
        react.createElement('input', {
          type: 'text',
          value: field.text,
          disabled: disabled === true,
          readOnly: readOnly === true || undefined,
          'aria-invalid': invalid || undefined,
          style: inputStyle(invalid, readOnly),
          onChange: (e) => onEdit(e.target.value)
        }),
        invalid ? react.createElement('p', { style: S.error }, props.invalidText) : null);
    }

    function BoolBox(props) {
      return react.createElement('input', {
        type: 'checkbox',
        checked: props.checked,
        disabled: props.disabled === true,
        onChange: (e) => props.onChange(e.target.checked)
      });
    }

    function ProviderRow(props) {
      const { t, row, actions, writable } = props;
      const locked = row.reserved;
      const disabled = locked || !writable;
      const f = (name) => row.fields[name];
      const edit = (name) => (raw) => actions.edit('providers.' + row.id + '.' + name, raw);
      return react.createElement('tr', null,
        react.createElement('td', { style: S.td }, react.createElement(BoolBox, {
          checked: f('enabled').text === true, disabled, onChange: edit('enabled')
        })),
        react.createElement('td', { style: S.td },
          react.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: '4px' } },
            react.createElement('strong', { style: { fontSize: '12px' } }, row.id),
            locked ? react.createElement('span', { style: S.lockTag }, t('reservedLock')) : null,
            react.createElement(FieldInput, { field: f('operator'), disabled, onEdit: edit('operator') }))),
        react.createElement('td', { style: S.td },
          react.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: '2px' } },
            react.createElement('span', { style: { fontSize: '11px', whiteSpace: 'nowrap' } }, f('type').text || '-'),
            row.tunnel
              ? react.createElement('span', {
                  style: S.tunnelBadge,
                  title: 'ssh -L 127.0.0.1:' + (row.tunnel.localPort || '?') + ':' + (row.tunnel.remoteHost || '?') + ':' + (row.tunnel.remotePort || '?')
                }, t('tunnelBadge') + ' · ' + tunnelSummaryText(row.tunnel, t))
              : null)),
        react.createElement('td', { style: S.td }, react.createElement(FieldInput, {
          field: f('baseUrl'), disabled, invalid: f('baseUrl').invalid,
          invalidText: t('invalidEnabled'), onEdit: edit('baseUrl')
        })),
        react.createElement('td', { style: S.td },
          react.createElement(FieldInput, { field: f('apiKeyEnv'), disabled, readOnly: true, onEdit: () => {} }),
          react.createElement('p', { style: S.hint }, t('apiKeyEnvHint'))),
        react.createElement('td', { style: S.td }, react.createElement(FieldInput, { field: f('models'), disabled, onEdit: edit('models') })),
        react.createElement('td', { style: S.td, title: 'supportsLogprobs' }, react.createElement(BoolBox, {
          checked: f('supportsLogprobs').text === true, disabled, onChange: edit('supportsLogprobs')
        })),
        react.createElement('td', { style: S.td }, react.createElement(FieldInput, { field: f('note'), disabled, onEdit: edit('note') }))
      );
    }

    function RoleSelect(props) {
      const { t, label, field, options, actions } = props;
      const selected = options.some((o) => o.value === field.value && !o.disabled) ? field.value : '';
      return react.createElement('div', { style: S.roleRow },
        react.createElement('span', { style: S.roleLabel }, label),
        react.createElement('select', {
          value: selected,
          'aria-invalid': field.invalid || undefined,
          style: { ...S.select, ...(field.invalid ? S.inputInvalid : {}) },
          onChange: (e) => actions.edit(field.key, e.target.value)
        },
          react.createElement('option', { value: '' }, t('roleNone')),
          options.map((o) => react.createElement('option', { key: o.value, value: o.value, disabled: o.disabled },
            o.label + (o.disabled ? ' — ' + o.reason : '')))),
        field.invalid ? react.createElement('span', { style: S.error }, t('roleInvalid')) : null);
    }

    function LanghuanCard(props) {
      const [open, setOpen] = react.useState(false);
      const s = props.useLanghuanCard((snapshot) => snapshot);
      if (!s.available) return null;
      const t = props.t;
      const blocked = !s.dirty || s.invalid || s.saving || !s.writable;
      const providerRows = Object.values(s.providers);
      return react.createElement('li', { style: S.card },
        react.createElement('button', {
          type: 'button', style: S.header, 'aria-expanded': open,
          onClick: () => setOpen(!open)
        },
          react.createElement('span', { style: { flex: 1, minWidth: 0 } },
            react.createElement('div', { style: S.title }, t('cardTitle')),
            react.createElement('div', { style: S.desc }, t('cardDescription'))),
          s.dirty ? react.createElement('span', { style: S.pending }, t('unsaved')) : null),
        open ? react.createElement('div', { style: S.body },
          !s.writable ? react.createElement('p', { style: S.hint, role: 'status' }, t('readOnly')) : null,
          s.failed ? react.createElement('p', { style: S.error, role: 'alert' }, t('saveFailed')) : null,

          react.createElement('h4', { style: S.sectionTitle }, t('sectionProviders')),
          react.createElement('table', { style: S.table },
            react.createElement('thead', null, react.createElement('tr', null,
              ['colEnabled', 'colOperator', 'colType', 'colBaseUrl', 'colApiKeyEnv', 'colModels', 'colLogprobs', 'colNote']
                .map((k) => react.createElement('th', { key: k, style: S.th }, t(k))))),
            react.createElement('tbody', null,
              providerRows.length === 0
                ? react.createElement('tr', null, react.createElement('td', { colSpan: 8, style: S.td }, t('noProviders')))
                : providerRows.map((row) => react.createElement(ProviderRow, {
                    key: row.id, t, row, actions: props, writable: s.writable
                  })))),

          react.createElement('h4', { style: S.sectionTitle }, t('sectionRoles')),
          react.createElement(RoleSelect, {
            t, label: t('roleAnnotation'), field: s.annotation,
            options: s.roleOptions.annotation, actions: props
          }),
          react.createElement(RoleSelect, {
            t, label: t('roleDecision'), field: s.decision,
            options: s.roleOptions.decision, actions: props
          }),

          react.createElement('h4', { style: S.sectionTitle }, t('sectionSkillsDir')),
          react.createElement('code', { style: { fontSize: '12px' } }, s.skillsDir),
          react.createElement('p', { style: S.hint }, t('skillsDirHint')),

          react.createElement('div', { style: S.footer },
            react.createElement('button', {
              type: 'button', style: { ...S.button, ...S.buttonGhost },
              disabled: !s.dirty && !s.failed,
              onClick: () => props.discard()
            }, t('discard')),
            react.createElement('button', {
              type: 'button', style: { ...S.button, ...S.buttonPrimary },
              disabled: blocked,
              onClick: () => props.save()
            }, s.saving ? t('saving') : t('save')))) : null);
    }
    //#endregion

    //#region client plugin apply
    function apply(ctx) {
      const t = ctx.locale.bind(NS);
      ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'langhuan: locale dictionaries');
      const controller = new LanghuanCardController(ctx.settingsScope.bind({ namespace: SETTINGS_NS }), t);
      ctx.effect(() => () => controller.dispose(), 'langhuan: card controller');
      ctx.slots.inject('settings.plugin.item', function* () {
        yield ctx.slots.register({
          name: 'settings.plugin.item',
          key: SETTINGS_NS,
          locale: NS,
          inject: () => controller.inject()
        }, LanghuanCard);
      });
    }
    //#endregion

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  }
});

/**
 * lib/sync.js 单测 —— node:test（无 mocha）。运行：node --test test/
 *
 * 覆盖点：
 * - 默认无覆盖 → providers 为出厂 10 家（对真实 DEFAULT_PROVIDERS 断言）
 * - 覆盖 deepseek.baseUrl 生效，其余出厂字段不动
 * - disabled/reserved 语义不被无关节径的覆盖改动
 * - 合并不污染共享的 DEFAULT_PROVIDERS 模块对象
 *
 * 本文件是 CJS（package.json 无 "type":"module"），顶层 await 非法；
 * plugin-core 是 ESM，经 before 钩子里的 dynamic import 惰性加载。
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildConfigSlice, mergeProviderOverrides } = require('../lib/sync');

/** 真实出厂预置（plugin-core 是 ESM，测试同样 dynamic import）。 */
let DEFAULTS;
test.before(async () => {
  const core = await import('@langhuan/plugin-core');
  DEFAULTS = {
    providers: core.DEFAULT_PROVIDERS,
    annotationProvider: core.DEFAULT_ANNOTATION_PROVIDER,
    decisionProvider: core.DEFAULT_DECISION_PROVIDER,
    skillsDir: core.DEFAULT_SKILLS_DIR,
  };
});

test('默认无覆盖：providers = 出厂 10 家，角色/目录回落默认', () => {
  const slice = buildConfigSlice(
    { providers: {}, annotationProvider: 'deepseek', decisionProvider: 'deepseek', skillsDir: '~/.agents/skills' },
    DEFAULTS,
  );
  assert.equal(Object.keys(slice.providers).length, 10);
  assert.equal(slice.providers.deepseek.baseUrl, 'https://api.deepseek.com/v1');
  assert.equal(slice.annotationProvider, 'deepseek');
  assert.equal(slice.decisionProvider, 'deepseek');
  assert.equal(slice.skillsDir, '~/.agents/skills');
});

test('覆盖 deepseek.baseUrl 生效，其余出厂字段原样保留', () => {
  const slice = buildConfigSlice(
    { providers: { deepseek: { baseUrl: 'https://proxy.example.com/v1' } } },
    DEFAULTS,
  );
  assert.equal(slice.providers.deepseek.baseUrl, 'https://proxy.example.com/v1');
  assert.equal(slice.providers.deepseek.apiKeyEnv, 'DEEPSEEK_API_KEY');
  assert.deepEqual(slice.providers.deepseek.models, ['deepseek-chat', 'deepseek-reasoner']);
  assert.equal(slice.providers.deepseek.enabled, true);
  // 其他运营商未被波及
  assert.equal(slice.providers.tencent.reserved, true);
});

test('disabled/reserved 语义不动：只改 note 不翻转 tencent 的 reserved/disabled', () => {
  const slice = buildConfigSlice(
    { providers: { tencent: { note: '临时备注' } } },
    DEFAULTS,
  );
  assert.equal(slice.providers.tencent.note, '临时备注');
  assert.equal(slice.providers.tencent.reserved, true);
  assert.equal(slice.providers.tencent.enabled, false);
});

test('显式启用 ssh_selfhost：enabled 翻转，reserved 仍为 false，缺省字段兜底', () => {
  const slice = buildConfigSlice(
    { providers: { ssh_selfhost: { enabled: true, sshHost: 'gw.example.com', sshUser: 'ubuntu' } } },
    DEFAULTS,
  );
  assert.equal(slice.providers.ssh_selfhost.enabled, true);
  assert.equal(slice.providers.ssh_selfhost.reserved, false);
  assert.equal(slice.providers.ssh_selfhost.sshHost, 'gw.example.com');
  assert.equal(slice.providers.ssh_selfhost.localPort, 8377);
});

test('合并不污染共享的 DEFAULT_PROVIDERS 模块对象', () => {
  buildConfigSlice({ providers: { deepseek: { baseUrl: 'https://x.example.com' }, brand_new: { operator: 'X' } } }, DEFAULTS);
  assert.equal(DEFAULTS.providers.deepseek.baseUrl, 'https://api.deepseek.com/v1');
  assert.equal(DEFAULTS.providers.brand_new, undefined);
});

test('未知 provider id 原样收录（用户自建运营商）', () => {
  const slice = buildConfigSlice(
    { providers: { my_gateway: { operator: '自研网关', baseUrl: 'https://gw.internal/v1', apiKeyEnv: 'GW_API_KEY' } } },
    DEFAULTS,
  );
  assert.equal(slice.providers.my_gateway.operator, '自研网关');
  assert.equal(Object.keys(slice.providers).length, 11);
});

test('非对象覆盖（数组/null/标量）被忽略，不抛错', () => {
  for (const bad of [null, undefined, [], 'deepseek', 42]) {
    const merged = mergeProviderOverrides(DEFAULTS.providers, bad);
    assert.equal(Object.keys(merged).length, 10);
  }
});

test('角色槽：空串透传（=未配置），undefined 回落出厂默认', () => {
  const empty = buildConfigSlice(
    { providers: {}, annotationProvider: '', decisionProvider: '' },
    DEFAULTS,
  );
  assert.equal(empty.annotationProvider, '');
  assert.equal(empty.decisionProvider, '');

  const fallback = buildConfigSlice({}, DEFAULTS);
  assert.equal(fallback.annotationProvider, DEFAULTS.annotationProvider);
  assert.equal(fallback.decisionProvider, DEFAULTS.decisionProvider);
  assert.equal(fallback.skillsDir, DEFAULTS.skillsDir);
});

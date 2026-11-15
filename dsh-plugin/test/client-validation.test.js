'use strict';

import test from 'node:test';
import assert from 'node:assert/strict';

let clientFactory;
global.window = {
  __ModuleLoader__: {
    load(definition) {
      if (definition.id === '@langhuan/dsh-plugin') clientFactory = definition.factory;
    },
  },
};
await import('../lib/client.js');
delete global.window;
const client = clientFactory(() => ({}));

function provider(overrides = {}) {
  return {
    operator: 'DeepSeek',
    type: 'openai_compatible',
    baseUrl: 'https://api.deepseek.com/v1',
    apiKeyEnv: 'DEEPSEEK_API_KEY',
    models: ['deepseek-chat'],
    enabled: true,
    supportsLogprobs: true,
    reserved: false,
    note: '',
    ...overrides,
  };
}

function createCard(entry = provider()) {
  const value = {
    providers: { deepseek: entry },
    annotationProvider: '',
    decisionProvider: '',
    skillsDir: '/tmp/skills',
  };
  const base = structuredClone(value);
  const mutateCalls = [];
  let registration;
  const scope = {
    getSnapshot: () => ({ status: 'ready', writable: true, value, base, user: {} }),
    subscribe: () => () => {},
    mutate: async (operations) => {
      mutateCalls.push(operations);
      for (const operation of operations) {
        let node = value;
        for (const segment of operation.path.slice(0, -1)) node = node[segment];
        const key = operation.path.at(-1);
        if (operation.op === 'unset') delete node[key];
        else node[key] = operation.value;
      }
      return true;
    },
  };
  const ctx = {
    locale: {
      bind: () => (key) => key,
      register: () => {},
    },
    effect: (callback) => callback(),
    get: (name) => name === 'configForms' ? {
      get: () => scope,
      whileServed: (_ids, callback) => callback(),
    } : undefined,
    slots: {
      inject: (_name, factory) => factory(),
      register: (options) => {
        registration = options;
        return options;
      },
    },
  };
  client.apply(ctx);
  const actions = registration.inject();
  return {
    actions,
    mutateCalls,
    snapshot: () => actions.hooks.langhuanCard.getSnapshot(),
  };
}

test('合法 Base URL 草稿可保存并清空 staging', async () => {
  const card = createCard();
  card.actions.edit('providers.deepseek.baseUrl', 'https://gateway.example.com/v1');
  assert.equal(card.snapshot().invalid, false);
  await card.actions.save();
  assert.equal(card.mutateCalls.length, 1);
  assert.equal(card.snapshot().dirty, false);
});

test('畸形或非 HTTP Base URL 标红对应字段且保存不调用 Host', async () => {
  for (const value of ['ftp://example.com/v1', 'not a url', 'https:///missing-host']) {
    const card = createCard();
    card.actions.edit('providers.deepseek.baseUrl', value);
    const snapshot = card.snapshot();
    assert.equal(snapshot.invalid, true);
    assert.equal(snapshot.providers.deepseek.fields.baseUrl.invalid, true);
    await card.actions.save();
    assert.equal(card.mutateCalls.length, 0);
    assert.equal(card.snapshot().dirty, true);
  }
});

test('disabled 占位 URL 不阻断其他草稿', () => {
  const card = createCard(provider({ enabled: false, baseUrl: 'https://<disabled>/v1' }));
  card.actions.edit('providers.deepseek.note', 'local note');
  assert.equal(card.snapshot().invalid, false);
});

test('既有端口错误不能被无关编辑绕过', async () => {
  for (const [field, value] of [['sshPort', 0], ['remotePort', -1], ['localPort', 65536]]) {
    const card = createCard(provider({ [field]: value }));
    card.actions.edit('providers.deepseek.note', 'unrelated');
    assert.equal(card.snapshot().invalid, true, field);
    await card.actions.save();
    assert.equal(card.mutateCalls.length, 0, field);
  }
});

test('stdio 非零端口在客户端阻止保存', async () => {
  const card = createCard(provider({ type: 'stdio', baseUrl: '', apiKeyEnv: '', localPort: 7379 }));
  card.actions.edit('providers.deepseek.note', 'unrelated');
  assert.equal(card.snapshot().invalid, true);
  await card.actions.save();
  assert.equal(card.mutateCalls.length, 0);
});

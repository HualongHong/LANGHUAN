'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { mkdirSync, mkdtempSync, readFileSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join } = require('node:path');

let core;
test.before(async () => {
  core = await import('@langhuan/plugin-core');
});

function validConfig() {
  return {
    providers: structuredClone(core.DEFAULT_PROVIDERS),
    annotationProvider: core.DEFAULT_ANNOTATION_PROVIDER,
    decisionProvider: core.DEFAULT_DECISION_PROVIDER,
    skillsDir: '',
  };
}

test('出厂配置通过，disabled 占位 URL 不阻断 apply', () => {
  const config = validConfig();
  assert.match(config.providers.scnet_model_deploy.baseUrl, /<scnet-endpoint>/);
  assert.deepEqual(core.validateConfig(config), []);
});

test('自定义 provider 省略端口时沿用既有默认', () => {
  const config = validConfig();
  config.providers.my_gateway = {
    type: 'openai_compatible', baseUrl: 'https://gw.internal/v1',
    apiKeyEnv: 'GW_API_KEY', enabled: true,
  };
  assert.deepEqual(core.validateConfig(config), []);
});

test('启用入口拒绝非 http(s) 与畸形 endpoint', () => {
  for (const baseUrl of ['ftp://example.com/v1', 'not a url', 'https:///missing-host']) {
    const config = validConfig();
    config.providers.deepseek.baseUrl = baseUrl;
    const errors = core.validateConfig(config);
    assert.ok(errors.some((error) => error.includes('INVALID_INPUT') && error.includes('baseUrl')));
  }
});

test('端口必须是整数且在允许区间，0 仅用于 remote/local 未配置', () => {
  for (const field of ['sshPort', 'remotePort', 'localPort']) {
    for (const value of [-1, 65536, 1.5, true]) {
      const config = validConfig();
      config.providers.deepseek[field] = value;
      const errors = core.validateConfig(config);
      assert.ok(errors.some((error) => error.includes('INVALID_INPUT') && error.includes(field)));
    }
  }
  const config = validConfig();
  Object.assign(config.providers.deepseek, { sshPort: 1, remotePort: 0, localPort: 65535 });
  assert.deepEqual(core.validateConfig(config), []);
});

test('stdio 禁止任何非零端口', () => {
  const config = validConfig();
  config.providers.local_stdio = {
    type: 'stdio', baseUrl: '', apiKeyEnv: '', enabled: false,
    sshPort: 0, remotePort: 0, localPort: 0,
  };
  assert.deepEqual(core.validateConfig(config), []);
  config.providers.local_stdio.localPort = 7379;
  const errors = core.validateConfig(config);
  assert.ok(errors.some((error) => error.includes('stdio') && error.includes('localPort')));
});

test('INVALID_INPUT 在底层同步处中止且不覆盖既有 providers.json', () => {
  const root = mkdtempSync(join(tmpdir(), 'langhuan-validation-'));
  const config = validConfig();
  const target = core.resolveProvidersPath(root);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, 'original', 'utf8');
  config.providers.deepseek.baseUrl = 'ftp://example.com/v1';
  assert.throws(
    () => core.syncProvidersJson(root, config),
    (error) => error.code === 'INVALID_INPUT' && error.message.includes('baseUrl'),
  );
  assert.equal(readFileSync(target, 'utf8'), 'original');
});

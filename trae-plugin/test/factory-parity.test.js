'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const FIELD_MAP = {
  operator: 'operator',
  type: 'type',
  baseUrl: 'base_url',
  apiKeyEnv: 'api_key_env',
  sshHost: 'ssh_host',
  sshPort: 'ssh_port',
  sshUser: 'ssh_user',
  sshKeyPath: 'ssh_key_path',
  remoteHost: 'remote_host',
  remotePort: 'remote_port',
  localPort: 'local_port',
  models: 'models',
  enabled: 'enabled',
  supportsLogprobs: 'supports_logprobs',
  reserved: 'reserved',
  note: 'note',
};

test('plugin-core 与 Python providers.json 出厂表逐字段一致', async () => {
  const { DEFAULT_PROVIDERS } = await import('@langhuan/plugin-core');
  const factoryPath = join(__dirname, '../..', 'skills/lit-scout-harvester/config/providers.json');
  const factory = JSON.parse(readFileSync(factoryPath, 'utf8'));
  assert.deepEqual(
    Object.keys(DEFAULT_PROVIDERS).sort(),
    factory.providers.map((entry) => entry.id).sort(),
  );
  for (const pythonEntry of factory.providers) {
    const expected = {};
    for (const [jsField, pythonField] of Object.entries(FIELD_MAP)) {
      expected[jsField] = pythonEntry[pythonField];
    }
    assert.deepEqual(DEFAULT_PROVIDERS[pythonEntry.id], expected, pythonEntry.id);
  }
});

/**
 * 零依赖 Standard Schema v1 配置校验器（@standard-schema/spec 形态），
 * 供不解析 schemastery 的宿主或纯文件驱动场景复用。
 *
 * 语义与 dsh-plugin 的 canonical schemastery schema 完全一致（同一
 * resolveConfigValue 实现，见 ./resolve-config.js）。
 */
import { resolveConfigValue } from './resolve-config.js';

export function buildStandardSchemaConfig() {
  return {
    '~standard': {
      version: 1,
      vendor: 'langhuan',
      validate(value) {
        const { value: resolved, issues } = resolveConfigValue(value);
        return issues.length ? { issues } : { value: resolved };
      },
    },
  };
}

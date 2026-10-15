/**
 * @langhuan/dsh-plugin 浏览器半侧类型（lazy-CJS factory 的模块导出形态）。
 * 运行时被 DSH 客户端模块系统装载；组件 props 由 slot 渲染机制合成
 * （hooks → use<Name> 选择器钩子，其余 inject 成员平铺）。
 */
import type { Context } from '@deepseek-ai/cordis';

/** 客户端插件依赖的浏览器侧服务。 */
export const inject: ['slots', 'locale', 'settingsScope'];

/** 浏览器半侧入口（DSH 客户端模块系统在页面 cordis 树中调用）。 */
export function apply(ctx: Context): void;

"""agentkit — LangHuan 套件共享脚手架库。

从各成员技能抽取的通用能力，按技能 zip 构建时 vendor 进分发包，
运行时自包含、不依赖包索引。

模块规划（抽取顺序见 docs/INTEGRATION_PLAN.md 第 6 节）：

- ``agentkit.llm``         【已实现 P1a】LLM provider 注册表 + 统一客户端
                           （缓存/重试/fallback/用量统计）+ 响应清洗 +
                           通用 AgentBridgeProvider + chat_sync 同步包装；
                           HTTP provider 延迟导入 aiohttp（可选 extra [llm]）
- ``agentkit.bridge``      【已实现 P0a】宿主 Agent 磁盘工单队列协议（零 API key，
                           collect/apply/strict 三模式，kind 注册表可扩展）
- ``agentkit.agent_entry`` 【已实现 P0b】非交互 Agent 入口骨架（标准参数组、
                           workdir 受控重定向、stdout JSON 摘要、退出码约定）
- ``agentkit.config``      【已实现 P2】.env 显式加载（load_env，无 import 期副作用）
                           + YAML extends/深合并/${VAR:default} 插值（load_yaml）；
                           技能特异的 Pydantic section 模型留在各技能
- ``agentkit.cache``       【已实现 P1a】SQLite 内容哈希缓存（Windows 句柄安全）
- ``agentkit.citation``    【已实现 P2】CITATION.md 血缘生成（模板参数化，
                           默认模板即 LSH 现有输出格式）
- ``agentkit.review``      人工复核队列与核验回路（任务包/裁决双 JSON 协议）
- ``agentkit.report``      Markdown 条目解析与结构化报告 lint 框架（规则可注册）
- ``agentkit.packaging``   【已实现 P4】技能无关 skill zip 构建器（暂存复制 +
                           vendor agentkit + 可选 wheel 降级 + MANIFEST.json +
                           绿包 install.sh 模板），套件入口为根 build_plugin.py
- ``agentkit.quota``       【已实现 P3】在线源额度治理原语：预算推算
                           （QuotaBudget.plan 需求→credits→天数）、窗口跟踪
                           （QuotaTracker 读 X-RateLimit-* 低阈值主动停）、
                           游标断点续抓去重（CursorStore）、429/403 长退避表；
                           纪律为模块级常量（不伪造 UA/不并发/不代理绕过）
- ``agentkit.fetch``       【已实现 P3】通用同步 HTTP 抓取层（urllib）：显式
                           跟随跨域重定向并记录链路防环路、Accept 内容协商、
                           二进制 content-type 不拒收原样返回、按 quota 退避表
                           重试；transport 可注入（测试用 fake）
- ``agentkit.credentials`` 【已实现 D7】credential provider 接缝：为宿主
                           credential broker（如 DSH Capsule）预留的可选适配层
                           （resolve(ref) + LeaseRef 租约透传记录）；无 broker 时
                           fail-open 回落 .env 路径（EnvCredentialProvider），
                           Capsule 永不成为硬依赖；接入点在 llm.base（api_key
                           为空时才经链解析，显式配置的 key 永不被覆盖）
- ``agentkit.logging``     【已并入 agent_entry，P2 评估后不单独建模块】
                           agent_entry.configure_logging（stderr 日志 / --quiet
                           映射 WARNING）已覆盖套件当前全部日志需求；stdout 数据 /
                           stderr 日志分离由 agent_entry 的 stdout JSON 契约保证
"""

__version__ = "0.1.0.dev0"

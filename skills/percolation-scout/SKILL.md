---
name: langhuan-percolation-scout
version: 0.1.0
description: >
  LangHuan Suite · 渗流探索技能（Percolation-Scout v0.1.0）。把格点渗流的
  相变直觉移植为研究探索纪律：在未知空间中先广度采样至"渗流饱和"（可计算的
  四维覆盖率门禁，阈值按领域校准——正方格 site 的 0.5927 仅为记忆锚点，阈值
  本身不普适），然后才在已探索团簇间检索弥合鸿沟的桥接通路，输出桥接候选清单
  （承接 GAP/SD 编号与八字段标注规范）。
whenToUse: >
  当需要在两个已知区域（理论、团队、技术路线、文献集合）之间的未知空间中
  判定"是否存在通路、通路在哪"之前，先量化探索是否充分时使用；适用于文献
  综述饱和度判定、专利/技术布局扫描、选题论证、技术路线设计的探索-桥接
  两阶段工作流，以及为 AVS/PCS 的 Phase 3 扫描提供渗流饱和出口门禁。
triggers:
  - 探索覆盖率评估 / 渗流饱和判定
  - 文献综述饱和度 / 是否搜够了
  - 两个领域之间的鸿沟弥合 / 桥接路径检索
  - 先广探再桥接的研究设计
  - AVS/PCS Phase 3 扫描出口门禁
  - 桥接候选清单生成
category: research-tools
author: LangHuan Suite
license: MIT
---

# Percolation-Scout（渗流探索）

> 物理原型：正方格点渗流阈值 p_c ≈ 0.592746（数值解）。**注意两个校准事实**：
> ① 它无闭式解析值；② **阈值不普适**（bond 正方格 0.5、三角格 0.5、三维立方格
> ~0.3116；普适的是临界指数与相变结构）。因此本技能出口的是**相变结构纪律**
> （"覆盖率不足时检索不到通路是假象，不是真鸿沟"），而不是 59.27% 这个数本身。

## 铁律

1. **禁止早桥接**：四维覆盖率未过门禁且盲区登记不完整时，禁止输出
   "两端无通路/合作不可行"类结论——那是采样假象（假阴性）。
2. **阈值须校准**：默认锚点 0.5927 只作起点；按领域校准并回测
   （methodology/coverage-metrics.md §3）。
3. **双向轨制继承**：D3-D6 类数据（在研项目/团队/设施/合作历史）LLM 不可自行
   检索，须人工喂入（继承 AVS/PCS 六类数据分类）。

## 两阶段工作流

详见 `percolation-scout/methodology/explore-bridge-workflow.md`：

1. **探索期**：按 D1-D6 信源矩阵采样；用 `scripts/coverage_report.py` 度量
   四维覆盖率（time/source/topic/volume，含均匀度惩罚）；停止判据 = 渗流饱和
   门禁（§4：四维达标 + 综合达标 + 盲区登记完整）。
2. **桥接期**：在已探索集合内检索 A×B 桥边，输出桥接候选清单（八字段标注、
   SD- 编号承接上游 GAP）；清单为空 → 判真鸿沟，回探索期定向补采。

## 覆盖率工具

```bash
# 吃 LSH 结构化题录，出报告
python scripts/coverage_report.py --input works.csv --out report.json
# 门禁判定（退出码 0=可进桥接期，1=继续探索）
python scripts/coverage_report.py --input works.csv --gate \
    --thresholds thresholds.json   # 领域校准阈值
```

输入契约与默认目标值见 `percolation-scout/methodology/coverage-metrics.md`。

## 与套件技能联动

| 伙伴 | 接口 |
|------|------|
| lit-scout-harvester | 题录结构化的计量底座：本技能的输入直接消费 LSH 的 CSV/JSONL 产出；LSH 的 `audit_sampling` 可复用为覆盖采样器 |
| peer-collab-strategizer | Phase 3 生态位扫描的出口条件改由本技能门禁承接；桥接候选清单直接喂 Phase 4 合成 |
| anchor-verify-strategizer | 竞争格局扫描同理：Phase 3→4 门禁 + 桥边（供应链/共现合作）清单 |
| 反向回流 | PCS/AVS 扫描登记的 GAP-XX 回流本技能探索期定向补采 |

## 内容目录

| 路径 | 内容 |
|------|------|
| `percolation-scout/methodology/explore-bridge-workflow.md` | 两阶段工作流、采样矩阵、桥接纪律、门禁规则 |
| `percolation-scout/methodology/coverage-metrics.md` | 四维覆盖度定义、均匀度惩罚、阈值校准指南 |
| `scripts/coverage_report.py` | 覆盖率报告 + 门禁判定（零依赖） |

# LangHuan Gazetteer Sample

## Introduction

LangHuan 套件用于文献地名归一化的**内置样例**数据候选包，包含常见国家、首都、地区的中英文别名，以及国家名称到三位国家码的映射。它不是 GeoNames 全量导出，不应被当作生产级全球地名库。

- **数据来源**：LangHuan 项目内置样例与人工维护别名表；原始来源和再分发许可尚待逐项核实。
- **数据量与格式**：一个 Gazetteer JSON 和一个国家别名 JSON，UTF-8。
- **适用任务**：文献信息抽取、地名规范化和离线地理映射测试。

## Files

### raw/sample_gazetteer.json

顶层含 version/description/places。每个 place 字段：

- name：规范地名；
- country_code / country_name：所属国家码与名称；
- type：country/city/region 等条目类型；
- aliases：中英文别名数组。

### raw/country_aliases.json

对象映射，键为归一化国家名称或别名，值为三位国家码。该表包含标准 ISO 码和项目内部使用的特殊码，不能仅凭三位格式推断全部为 ISO 3166 正式代码。

## Data preprocessing

查询时执行 Unicode 去重音、casefold、标点/空白归一化；同名别名按文件顺序先注册者优先。数据预处理代码位于 `lit_scout_harvester/external/gazetteer.py` 和 `utils/country.py`。

## Offline preflight and packaging

从仓库根目录运行：

```bash
python3.14 -B scripts/dataset_pack.py --manifest data/opendatalab/gazetteer/dataset.json --check-only
python3.14 -B scripts/dataset_pack.py --manifest data/opendatalab/gazetteer/dataset.json --output-dir output/langhuan-gazetteer
```

输出目录必须不存在或为空。预检同时覆盖 README/metafile 的路径与疑似密钥，载荷 target 按跨平台大小写不敏感规则去重；发布报告不记录构建机输出路径。

## Release blocker

`metafile.yaml` 当前 deliberately 使用 `license: UNVERIFIED`。公开上传前必须：

1. 核实所有别名来源、特殊国家码含义与再分发权；
2. 将 license 改为实际适用的协议并补充来源/引用；
3. 决定这是继续标注 sample，还是替换为经许可的生产数据集；
4. 再运行 `scripts/dataset_pack.py` 预检，确保零 ERROR。

本目录只用于离线准备，不表示已经公开发布。生成且许可核验通过的包可在 LSH 中配置 `modules.geocoder.gazetteer_package`，加载器会复核哈希与许可证；校验不等于发布者身份认证。

## Citation

数据来源与引用待补全，未完成前不得公开上传。

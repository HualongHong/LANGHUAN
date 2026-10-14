"""自动化评价：黄金测试集加载、设定书 §4 指标计算、CI 集成。"""

from .metrics import disagreement_stats, schema_compliance

__all__ = ["schema_compliance", "disagreement_stats"]

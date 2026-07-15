# Func-Predict

```text
Func-Predict/
├── run.py                 # 策略日荐（信号日 → 下一交易日开盘）
├── daily_update.py        # 行情库盘后增量更新
├── utils/                 # 日荐注册 / adapter / 落盘
├── output/                # 日荐报告（按执行日）
├── strategies/            # 策略文件夹
└── a_share_quant_data/    # A 股研究数据
```

## 常用

```bash
# 盘后更新：行情库 + 北向/两融 + 重建信号面板（不跑日荐）
python3 daily_update.py
python3 daily_update.py --skip-slow     # 数据只跑行情链路
python3 daily_update.py --data-only     # 只更新库，不重建面板

# 日荐（独立；daily_update 跑完后即可）
python3 run.py crowding_price_volume
# → output/<strategy>/<exec_date>/report.md
# 默认读 strategies/.../cache/signals_*_v1.0.parquet
```

数据细节见 [`a_share_quant_data/README.md`](a_share_quant_data/README.md)。  
策略细节见 [`strategies/crowding_price_volume/README.md`](strategies/crowding_price_volume/README.md)。

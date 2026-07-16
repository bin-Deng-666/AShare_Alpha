# A 股五日区间收益：涨跌停触及—封板状态特征

研究日期：2026-07-17（Asia/Shanghai）。本文只做事前研究定义，不读取目标列、候选分数或回测收益。唯一开发选择区间为 2021-01-01—2023-12-31，行情与标签的物理读取上限为 2023-12-31；2024-01-01 以后数据严禁参与候选方向、公式、窗口、缺失处理、门槛或胜负判断。

## 唯一建议

下一轮只登记一个不可拆分的五项 bundle：

1. `limit_up_close_share_5`：最近五个市场交易日中收盘封住涨停的比例；
2. `limit_up_unsealed_share_5`：最近五个市场交易日中盘中触及涨停、但收盘未封住的比例；
3. `limit_down_close_share_5`：最近五个市场交易日中收盘封住跌停的比例；
4. `limit_down_unsealed_share_5`：最近五个市场交易日中盘中触及跌停、但收盘未封住的比例；
5. `limit_history_missing_5`：上述五日规则历史是否不完整。

四项比例必须一起进入 Elastic Net 和浅层 GBRT；缺失标记也属于 bundle。不得在看到开发结果后只保留涨停、删除跌停、合并“触及”和“封板”、把五日改成一日或二十日，或给某一事件人工指定多空方向。五日窗口只因为它与冻结的 `T+1 open -> T+6 open` 标签和五日 sleeve 完全对齐，不是从多个窗口中回测挑出的。

这是本轮优先级最高的候选，因为价格限制是 A 股制度中逐日变化的离散市场状态，而当前 35 个默认输入没有表达“价格是否真的碰到当日交易所边界”或“碰到后是否封住”。公开研究对涨停和跌停后的延迟反应、反转及流动性干扰给出不完全一致、且方向不对称的证据；这正适合用四个分开的状态输入交给已冻结的浅层非线性模型，而不适合硬编码成一个单向因子。

## 一手证据与候选取舍

### 价格限制后的反应不是普通收益窗口

Wan 等使用沪深全部普通 A 股的高频数据，分别记录开盘、上午、下午及收盘触及价格限制的事件，并发现限制日后的次日价格延续比反转更常见，跌停事件尤其明显。这直接支持区分上涨/下跌方向和“收盘仍在限制价”的状态，但其样本是 2000—2011 年、结论主要针对次日，不能直接证明本仓库未来五日组合会增益。[Wan et al., *Statistical Properties and Pre-Hit Dynamics of Price Limit Hits in the Chinese Stock Markets*（PLOS ONE 原文）](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0120312)

Wu、Wang 与 Li 专门分析价格触限后重新打开的 A 股事件，报告次日连续触限概率较高，事件后的预期收益形状还受市场趋势和连续触限时长影响。这为把“触及但收盘未封住”与收盘封板分开提供直接依据，也说明不能把它们合并后强加同一方向。[Wu, Wang & Li, *Post-hit dynamics of price limit hits in the Chinese stock markets*（期刊原文）](https://www.sciencedirect.com/science/article/pii/S0378437116305659)

Wong、Liu 与 Zeng 使用上交所逐笔数据，报告涨停后的延迟价格发现、跌停后的反转，并观察到接近跌停时成交量和单笔规模降低、价差扩大。它与 Wan 等对跌停后方向的结果并不完全一致，因此本 bundle 把涨停与跌停、封板与未封板分别交给 GBRT，禁止预设“涨停追涨”或“跌停抄底”的固定符号。[Wong, Liu & Zeng, *Can price limits help when the price is falling?*（期刊原文）](https://www.sciencedirect.com/science/article/pii/S1043951X08000680)

Chen 等使用深交所账户级数据发现，大投资者在股票触及 10% 涨停当日净买入、次日卖出，并且该净买入与更强的长期反转相关。这个结果说明同一个涨停事件可以同时包含很短期的持续交易和较长期的反转风险，进一步反对把事件简单并入现有 `reversal_20` 或赋予固定方向。[Chen et al., *Daily Price Limits and Destructive Market Behavior*（NBER 原文）](https://www.nber.org/system/files/working_papers/w24014/w24014.pdf)

Chen、Gu 与 Ni 利用 2020 年创业板涨跌幅限制由 10% 放宽至 20% 的准自然实验，发现限制放宽改善价格效率，并在事件研究中观察到价格延续和价格延迟下降；卖空约束又会改变这一效果。本仓库没有完整的历史可卖空股票区间，因此不能照搬其分组，只能把逐日价格限制事件作为 PIT 状态输入，并把不可观测的卖空状态列为解释限制。[Chen, Gu & Ni, *How price limit affects the market efficiency in a short-sale constrained market?*（期刊原文）](https://www.sciencedirect.com/science/article/pii/S0927539823000476)

交易规则本身也要求使用逐日实际边界，而不是把所有接近 `+10%/-10%` 的收益都当成同一种事件：上交所科创板股票上市后通常为 20% 限制、前五个交易日不设限制；深交所创业板规则同样规定 20% 和 IPO 前五个交易日不设限制。[上交所科创板交易机制官方说明](https://star.sse.com.cn/star/en/infodisclosure/newsrelease/c/c_20190723_4867909.shtml)；[深交所创业板特别交易规定（官方 PDF）](https://investor.szse.cn/English/rules/siteRule/P020200811392728112984.pdf)。开发期内主板、科创板和创业板限制不同，ST 规则也不同，所以公式必须使用逐日实际限制价，不能从前复权收益率猜测限制事件。

更关键的是，深交所 2023 年官方交易规则第 4.4.1—4.4.3 条规定，除权除息日应以除权（息）参考价作为涨跌幅基准，而不是机械使用上一日原始收盘价；规则还给出 `[(前收盘价-现金红利)+配股价格×股份变动比例]/(1+股份变动比例)` 的标准公式，并允许发行人在交易所同意后公告调整公式。[《深圳证券交易所交易规则（2023 年修订）》官方 PDF](https://docs.static.szse.cn/www/lawrules/rule/trade/W020230217564423808793.pdf)。因此，本候选虽有足够原始字段，但**当前派生限制价必须先修复和验证，正式赛马才能开始**。

### 为什么不选另外两个方向

| 方向 | 本轮不选的原因 |
|---|---|
| 隔夜/日内收益分解 | 当前输入已有 `overnight_gap_mean_5`、`ret_5`、日内收盘位置和原始开收盘路径；再加入五日隔夜累计或日内累计可由这些变量近似恢复，边际信息弱。中国隔夜负收益的一手研究把现象与 T+1 交易规则联系起来，但这类市场总体微观结构事实不等于长-only 股票横截面可以按 `T+1 open` 无摩擦套利。[Qiao & Dam, *The overnight return puzzle and the T+1 trading rule*（期刊原文）](https://doi.org/10.1016/j.finmar.2020.100534) |
| 流动性条件反转 | 当前浅层 GBRT 已同时读取 `reversal_20`、异常换手、换手/成交额波动、换手冲击、Amihud、ADV、波动与 MAX，能够直接学习这些输入的阈值和交互；再手工加入 `reversal × liquidity` 只会重复现有信息并增加研究者自由度。 |
| 涨跌停触及—封板状态 | 现有输入没有交易所限制价事件；逐日限制价与原始 OHLC 已在冻结面板中，既能 PIT 构造，也与公开 A 股价格延迟、反转和交易干扰证据直接对应。因此固定为唯一候选。 |

## 固定公式

令 `T` 为信号日，`s` 为市场交易日；`H_raw(i,s)`、`L_raw(i,s)`、`C_raw(i,s)` 是股票 `i` 的未复权最高价、最低价、收盘价，`U(i,s)`、`D(i,s)` 是经过官方参考价规则重建并通过硬校验的未复权涨停价、跌停价。A 股最小价格单位为 `0.01` 元，因此先把所有价格四舍五入到分：`cent(x)=round(x,2)`。只在当日有交易、限制价存在且该日限制价通过下述数据门槛时定义事件：

```text
up_touch(i,s) = 1[trade_status(i,s)
                  and U(i,s) is finite
                  and cent(H_raw(i,s)) = cent(U(i,s))]

dn_touch(i,s) = 1[trade_status(i,s)
                  and D(i,s) is finite
                  and cent(L_raw(i,s)) = cent(D(i,s))]

up_close(i,s) = 1[trade_status(i,s)
                  and cent(C_raw(i,s)) = cent(U(i,s))]
dn_close(i,s) = 1[trade_status(i,s)
                  and cent(C_raw(i,s)) = cent(D(i,s))]

up_unsealed(i,s) = up_touch(i,s) * (1 - up_close(i,s))
dn_unsealed(i,s) = dn_touch(i,s) * (1 - dn_close(i,s))
```

实现必须先断言 `up_close <= up_touch`、`dn_close <= dn_touch`；若不成立，说明原始价格或限制价口径不一致，停止实验，不能把负的“未封板”计数截为零。同方向的“收盘封板”和“触及但未封板”互斥；同一交易日理论上可能先后触及上下两个边界，因此上下方向整体不强制互斥，也不强制四项之和小于等于一。

窗口固定为包含 `T` 的最近五个**市场交易日** `S_5(T)={T-4,...,T}`，不是股票最近五条非缺失记录。停牌日仍占一个市场交易日且事件为零；缺行、无规则限制、限制价没有经过官方参考价验证或任一必要原始字段不可判断时，整个窗口标为缺失，禁止跨过该日向更早记录补足五条：

```text
limit_up_close_share_5(i,T) = sum_s∈S5 up_close(i,s) / 5
limit_up_unsealed_share_5(i,T) = sum_s∈S5 up_unsealed(i,s) / 5
limit_down_close_share_5(i,T) = sum_s∈S5 dn_close(i,s) / 5
limit_down_unsealed_share_5(i,T) = sum_s∈S5 dn_unsealed(i,s) / 5

limit_history_missing_5(i,T) = 1[五个市场交易日的行、经验证限制价或必要原始字段不完整]
```

当 `limit_history_missing_5=1` 时，四项比例保持 `NaN`；冻结模型的缺失填零仅在同时保留缺失标记时才允许。四项比例天然位于 `[0,1]`，作为直接模型输入，**不做 1%/99% 缩尾，也不做每日横截面排名**。价格限制事件在每天通常不足横截面的 1%；若沿用连续特征的 99% 缩尾，非零事件可能全部被压成零。Elastic Net 与 GBRT 都读取四项原值和缺失标记；固定因子基线不为它们指定方向。

## 与现有 35 个输入的非重复性

- `ret_5/20/60`、`mom_20_5`、`mom_120_20`、`reversal_20` 记录实现收益路径，但同样的涨跌幅在主板、ST、科创板和创业板可能分别是触限、未触限或无当日限制；前复权收益还不能可靠重建交易所的未复权取整价格。
- `vol_20/60`、`maxret_20`、`downside_vol_20`、`range_vol_10`、回撤和趋势特征描述连续价格形态，不能区分“最高价碰到限制后打开”与“全天从未触限”。
- 换手、成交额、Amihud、ADV、价量相关和 `turnover_shock_5_20` 是流动性代理，但没有编码订单被硬边界截断或收盘仍封在边界的状态。
- `overnight_gap_mean_5` 和 `close_location_mean_10` 也不能替代限制价：收盘位于日内最高点不等于涨停，隔夜跳空不等于上一日价格发现被限制。
- EP、BP、ROE、规模、行业残差候选以及板块控制列没有事件信息；板块控制只能说明适用哪类制度，不能说明当日是否真正触及边界。

因此，这四项事件比例与现有收益、反转、波动、MAX、回撤、趋势、流动性、估值和八项增强特征会相关，但不是它们的重命名。开发报告仍必须输出新增列与全部既有输入的 Spearman/点二列相关、五项内部相关矩阵和模型分数稳定性；若增量只是在重复极端收益权重而没有经济改善，整个 bundle 失败。

## 仓库可实现性、当前阻塞与预期覆盖

仓库稳定数据层已把未复权限制价和收盘触限标记暴露到 `stock_daily/stock_daily_raw`，rotating 加载器又把 `limit_up`、`limit_down`、`is_limit_up`、`is_limit_down` 以及 `raw_high/raw_low` 带入 PIT 面板；`stock_daily_raw` 也有未复权收盘价。本轮审计开始时，rotating PIT 加载器尚未把它单独命名为 `raw_close`，因此正式实现须把 `raw_close` 一并显式带入，而不能用前复权 `close` 复算封板。字段口径见 [`data/README.md`](../../data/README.md) 和 [`strategy/rotating/lib/data.py`](../../strategy/rotating/lib/data.py)。

审计发现的真正阻塞在限制价参考基准。修复前的 `data/scripts/build_dataset.py::build_limit_prices` 一律使用上一条未复权收盘价乘以 `1±limit_pct`，没有处理官方除权（息）参考价。对冻结 v2 开发面板的无目标字段复核得到：2021—2023 年 1,751,687 个合格信号行虽然表面上 `limit_up/down` 覆盖率为 100%，却有 781 行 `raw_low < limit_down`，其中 776 行越界超过一分钱，逐年为 227/282/267 行；例如 `002846` 在 2021-03-30 的原始最低价为 10.46 元，而修复前派生下限为 16.00 元。真实交易不可能越过有效跌停价，这证明“非空”不等于“正确”。核验时 catalog 中 `corporate_actions` 为 0 行，`stock_daily_raw.pct_change` 也全空，单纯屏蔽已越界行无法发现小额现金分红造成、但尚未把 OHLC 推出错误边界的隐性偏差。

因此正式赛马前必须完成以下前置数据工作之一，并优先采用直接行情参考价：

1. 按仓库数据文档重新拉取 SH/SZ 原始日线的来源 `preclose`/当日行情显示前收盘价；BaoStock 日线 API 原生提供 `preclose` 字段，但仍须验证它在除权（息）日确实等于交易所参考价，经 781 个显性异常日和抽样公司行为逐项核对后才能用于计算 `U/D`；或
2. 拉取完整 `corporate_actions`，按交易所第 4.4.2 条及发行人公告的例外公式重建除权参考价，再与原始行情交叉验证。

两条路径都必须重建 `limit_up/down` 和收盘标记，并保存参考价来源与校验状态。只把修复前快照的 781 个显性越界日及其五日窗口设为缺失，仅可做 smoke test，**不能产生可采信的开发赛马结果**，因为它抓不到未越界的错误参考价。修复后才重新统计四类事件；此前的 36,507/23,364/11,873/6,917 只能说明事件量级，不能作为最终特征计数。

正式编码前的单票交叉核验还发现同一公司行为污染了旧 qfq：`002846` 在 2021-03-30 实施 10 转 6 后，旧 qfq 收盘从 17.87387 跳至 10.80，几乎保留了全部机械除权跌幅。由于现有 35 个输入和五日标签都使用 qfq 开收盘，正式实验还必须从 raw OHLC 与同一 ex-date 公司行为重建 qfq，再让 B/L 共同读取该修正快照；否则即使限制价正确，特征和标签仍会混入伪收益。本发现发生在读取任何修正后目标、候选分数或回测收益之前，不改变唯一候选或胜出门槛。

同理，组合引擎虽然使用 raw 价格模拟成交，却不能只让股价在 ex-date 机械下跳：已有持仓须在当日开盘前增加送转股数并入账现金红利，ex-date 才买入的订单不享有该次权益。B/L 必须共同采用这条执行修复并保存公司行为审计，否则回测 PnL 仍会把分配误报成亏损。
本策略持有期通常不超过一个月，因此现金红利按 20% 税率保守计提；国家税务总局现行政策规定一个月以内的股息红利所得全额计入应纳税所得额并适用 20% 税率。[国家税务总局、财政部、证监会《关于上市公司股息红利差别化个人所得税政策有关问题的通知》](https://www.chinatax.gov.cn/n810341/n810755/c1797427/content.html)

推送前边界审查进一步否决了“接口返回完整当前历史、客户端再截断”的做法。最终有效数据路径只使用服务端截止日查询：东方财富 `RPT_SHAREBONUS_DET` 以 `EX_DIVIDEND_DATE<=2023-12-31` 分页拉取分红送转，巨潮 `p_stock2232` 对每个代码传入 `sdate=1970-01-01`、`edate=2023-12-31` 拉取配股；证券主集合同时限制为截止日已上市的沪深代码。TDX `xdxr` 的完整当前历史结果不得用于本轮指标，即使落盘前会过滤日期也不满足物理读取边界。

第三次推送前审查又指出：只枚举分红送转与配股，仍无法证明缩股、代码迁移或其他未分类股本变化没有静默污染参考价。最终实现因此把 BaoStock 服务端 `end_date=2023-12-31` 的原始日线 `preclose` 作为逐交易日核验层，在 5,328 个冻结代码上检查 10,651,292 个参考行。公告公式与观测值不一致、无公告但观测前收价相对上一原始收盘发生变化、以及服务器参考价缺口都会生成独立的不支持状态；价格公式、持仓已知权益入账、事件日交易执行和标签能力分别建模。全量只出现 2 个无公告参考价缺口，均显式保守失效；若未来是已有公告但参考价缺失，则保留公告权益项但仍阻断另外三项能力。另识别 23 个疑似缩股、2,220 个未分类股本变化和 741 个公式不匹配事件。它们不能证明事件经济类型，但足以让未知机械跳变在对应能力上保守失效。配股则保留可核验的价格公式：开发评分行中 31 个配股日限制价全部有效且来源为 `corporate_actions_ex_right`，持仓仍可领取同日已知现金/送转但不自动认购配股，同时阻断事件日新订单和跨事件标签。公告参考价不匹配时也继续入账明确的现金/送转；若持仓跨过缩股、无公告参考价缺口或未分类变化等权益本身未知的事件，实验机器门槛直接失败。

正式数据门槛固定如下：

- 逐日参考价须有来源标记，除权（息）日须通过公司行为/行情 `preclose` 交叉校验；开发期内必须满足 `cent(raw_high) <= cent(U)` 和 `cent(raw_low) >= cent(D)`，任何违反都令该日无效并触发原因审计；
- `raw_close`、`raw_high`、`raw_low` 与重建 `limit_up/down` 均须使用未复权同口径，按分精确比较；
- 四项比例的有效五日窗口覆盖率须全期不低于 99.5%，且 2021、2022、2023 每年均不低于 99.0%；正式股票池至少 120 个上市交易日，因此在参考价源完整后预期接近 100%，但不得用这个预期代替实测；
- 每个年度四项事件列都必须既有零值也有非零值；
- `close <= touch` 断言、五日市场日历连续性和原有面板键/旧列逐值身份检查必须全部通过；
- 未满足任一项即判 `insufficient_data`，停止正式赛马；不允许改比较容差、改窗口、把缺失当作零或读取 2024+ 补样本。

## PIT 与可执行性

- 所有触及判断只比较同一口径的**未复权** OHLC 和未复权限制价；禁止拿前复权 `high/low/close` 与交易所限制价比较。
- 当日 `T` 的最高、最低、收盘状态到收盘后才完整可知，因此信号最早在 `T+1 open` 使用，正好与冻结标签一致。不能把盘中触及时间假装成收盘前可下单信号。
- 板块、ST、上市初期及规则变更全部由 `T` 日数据层的实际 `limit_up/down` 决定；禁止用当前证券名称、当前板块快照或固定 10% 向历史回填。
- `high/low` 只能证明当日曾触及边界，不能恢复首次触及时点、封板时长、排队量或逐笔订单。`unsealed` 仅表示“触及但未以限制价收盘”，不得在报告中冒充完整的炸板时长或订单簿信号。
- 特征不会放松现有成交门槛。候选若偏好涨停后股票，`T+1` 一字涨停买单仍须按引擎规则积压或取消；跌停后的持仓卖出也保留原有未成交处理。报告必须单列四种事件后的次日可买率、订单积压和实际成交率。
- 标签保持 `O(T+6)/O(T+1)-1`，`label_available_date=T+6`；每次季度训练只允许 `label_available_date <= prediction_date` 的行。不得改变目标、中性化或五日 sleeve 来迎合事件特征。

## 唯一开发实验与公共测试边界

只比较两个规格：

```text
B: 冻结 v0.2 的 35 个输入 + 五日端点标签 + 浅层 GBRT
L: B + 上述固定五项涨跌停触及—封板 bundle
```

`B` 与 `L` 必须读取同一份冻结开发面板。除新五列外，`(code,date)` 键、旧输入、目标、`label_available_date`、股票池、季度重训日期、三年训练窗、随机种子、模型超参数、五日 sleeve、容量、历史成本和成交规则逐项相同。不得重拉数据导致基准漂移，不做用于选择的单项 ablation，也不搜索事件窗口、容差、衰减、符号、交互或板块子样本。

| 边界 | 固定规则 |
|---|---|
| 特征暖身 | 可读取 2021 年前的历史行，仅用于冻结五日窗口和原模型训练暖身，不作为选择期表现 |
| 候选选择 | 仅 2021-01-01—2023-12-31 |
| 物理读取上限 | 所有行情、限制价和标签均不得晚于 2023-12-31 |
| 开发期末标签 | 只保留 `T+6 <= 2023-12-31` 的训练/评价样本 |
| 2024-01-01 以后 | 禁止读取、统计、画图、调参、改公式、拆 bundle、打破平局或作为新的留出验证 |

旧 2024+ 区间已被此前策略研究使用，不能因本候选胜出而重新宣称为未见样本。即使 `L` 通过全部开发门槛，也只能登记为 `development_winner_pending_fresh_forward_validation`，默认 v0.2 不在本实验中自动切换；后续证明必须来自新的前瞻纸面交易窗口。

## 固定通过门槛与失败条件

候选 `L` 只有同时满足以下全部条件才算开发期胜出：

1. 2021—2023 全期历史时变费用口径的净年化收益和 Sharpe 都严格高于 `B`；
2. 三个日历年度中至少两个年度的净收益和 Sharpe 同时严格高于 `B`；
3. 固定 20bp 往返成本压力下的净年化收益和 Sharpe 都严格高于 `B`；
4. 全期最大回撤不得比 `B` 恶化超过 2 个百分点，年化换手不得增加超过 25%；
5. 对实际五日端点收益的平均 Rank IC 不得比 `B` 下降超过 0.002，且五个不重叠 cohort 的候选 IC 均为正；
6. `L-B` 配对日收益均值必须为正；同时报告 HAC(4) t，但不以显著性阈值追加调参；
7. 上述覆盖、连续市场日历、`close <= touch`、旧列身份和执行审计全部通过。

任一门槛失败、逐年结果分裂、覆盖不足、事件列在任一年无变化、收益只来自不可成交的一字板，或报告不完整，均整体判为 `rejected_on_development` 并保留 v0.2。失败后不得拆包、改成只追涨停、切换至一日标签或查看 2024+ 寻找补救口径。

## 必报诊断

- 五项的全期与逐年覆盖、缺失原因、每日非零股票数、四类事件数，以及按主板/科创板/创业板的分布；
- `close <= touch`、五个市场交易日连续性、新旧面板键和全部旧列身份校验；
- 新增列与全部 35 个既有输入的相关矩阵、四项事件比例的内部矩阵，以及 Elastic Net/GBRT 分数相对基准的季度稳定性；
- 全期、逐年和五个不重叠 cohort 的 Rank IC，重叠日序列使用 HAC(4)；
- 历史时变费用及固定 20/40/80bp 往返压力下的收益、Sharpe、最大回撤、换手、未成交积压；
- 四类事件后的次日可买/可卖率、候选订单的实际成交率、积压金额和未执行收益贡献；
- `L-B` 配对日收益均值和 HAC(4)，以及明确的 `development_winner_pending_fresh_forward_validation` 或 `rejected_on_development` 决策。

这些分组仅用于诊断固定 bundle，不构成事后选择入口。实验必须作为新的策略尝试登记到现有尝试台账，以便后续 DSR/多重尝试审计纳入本轮。

## 一手来源

1. Wan et al., *Statistical Properties and Pre-Hit Dynamics of Price Limit Hits in the Chinese Stock Markets*: <https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0120312>
2. Wong, Liu & Zeng, *Can price limits help when the price is falling? Evidence from transactions data on the Shanghai Stock Exchange*: <https://www.sciencedirect.com/science/article/pii/S1043951X08000680>
3. Chen et al., *Daily Price Limits and Destructive Market Behavior*: <https://www.nber.org/system/files/working_papers/w24014/w24014.pdf>
4. Chen, Gu & Ni, *How price limit affects the market efficiency in a short-sale constrained market?*: <https://www.sciencedirect.com/science/article/pii/S0927539823000476>
5. Shanghai Stock Exchange, *Q&A on Trading Mechanism of SSE STAR Market in the Initial Stage of Opening*: <https://star.sse.com.cn/star/en/infodisclosure/newsrelease/c/c_20190723_4867909.shtml>
6. Shenzhen Stock Exchange, *Special Rules on Trading on the ChiNext Market*: <https://investor.szse.cn/English/rules/siteRule/P020200811392728112984.pdf>
7. 深圳证券交易所，《深圳证券交易所交易规则（2023 年修订）》: <https://docs.static.szse.cn/www/lawrules/rule/trade/W020230217564423808793.pdf>
8. Wu, Wang & Li, *Post-hit dynamics of price limit hits in the Chinese stock markets*: <https://www.sciencedirect.com/science/article/pii/S0378437116305659>
9. Qiao & Dam, *The overnight return puzzle and the T+1 trading rule*: <https://doi.org/10.1016/j.finmar.2020.100534>
10. BaoStock, *Python API 文档*: <https://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3>

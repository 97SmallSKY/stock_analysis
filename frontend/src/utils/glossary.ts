/**
 * P19.7 (2026-04-20): 专业术语字典
 *
 * 页面上所有可能让新手困惑的专业术语,通过 `TermTooltip` 组件悬浮/点击显示解释。
 *
 * 用法:
 *   <TermTooltip term="hot_money">游资</TermTooltip>
 *
 * 添加术语原则:
 *   - 用**大白话**解释,不用其他专业术语套娃
 *   - 举例比定义更好
 *   - 如果是评分/指标,说明**怎么算 + 怎么看**
 *
 * 分类 tag(供筛选/高亮用,可选):
 *   lhb     —— 龙虎榜相关
 *   score   —— 评分维度
 *   sentiment —— 市场情绪
 *   backtest —— 回测 / 风险指标
 *   market  —— 市场基础概念
 */

export interface Term {
  key: string              // 唯一键,英文/拼音,如 'hot_money'
  name: string             // 中文名(界面主要展示)
  aliases?: string[]       // 别名 / 英文缩写,用于查找兜底
  tag: 'lhb' | 'score' | 'sentiment' | 'backtest' | 'market'
  brief: string            // 一句话解释(悬浮默认展示)
  detail?: string          // 详细解释(点击展开,可选)
  example?: string         // 举例(可选)
}

export const GLOSSARY: Term[] = [
  // =============== 龙虎榜 ===============
  {
    key: 'lhb',
    name: '龙虎榜',
    aliases: ['龙虎榜日报', 'LHB'],
    tag: 'lhb',
    brief: '交易所每天盘后公布的「异动股票榜单」,展示当日买卖前 5 大营业部。',
    detail: '涨跌幅偏离 ±7%、换手率、振幅等触发条件达标的股票会上榜。榜单披露买卖前 5 名营业部及其成交金额,是追踪游资、机构资金流向的核心数据。',
    example: '贵州茅台当日涨幅 9%,上榜原因:「日涨幅偏离值达到 7%」,披露深股通、华泰南京等 5 个席位。',
  },
  {
    key: 'hot_money',
    name: '游资',
    aliases: ['hot money', '游资席位', '一线游资'],
    tag: 'lhb',
    brief: '出没在龙虎榜上的私募/个人投资者「知名营业部」,追题材、快进快出。',
    detail: '业内有一套公认的「游资席位」名单,如华泰证券总部、国泰君安南京太平南路、中信建投北京 4 中心等。当这些席位大额买入,通常被认为「游资炒作启动」。',
    example: '某股票当日涨停,龙虎榜首席买入来自「方新侠」旗下席位,次日大概率继续被资金追捧。',
  },
  {
    key: 'institution',
    name: '机构席位',
    aliases: ['机构专用', '机构'],
    tag: 'lhb',
    brief: '公募基金、社保、QFII 等「正规军」的营业部,出手通常代表**长期看好**。',
    detail: '龙虎榜席位名显示为「机构专用」即公募/社保/险资席位。与游资不同,机构资金换手慢,连续出现买入往往是中长线配置信号。',
  },
  {
    key: 'net_amount',
    name: '净买入',
    aliases: ['净买额'],
    tag: 'lhb',
    brief: '买入金额 − 卖出金额,正数代表资金净流入。单位:万元。',
    example: '某席位买入 5000 万,卖出 2000 万,净买入 = 3000 万(万元为单位即 3000)。',
  },
  {
    key: 'buy_amount',
    name: '买入额',
    tag: 'lhb',
    brief: '该席位当日的买入成交金额,万元为单位。',
  },
  {
    key: 'sell_amount',
    name: '卖出额',
    tag: 'lhb',
    brief: '该席位当日的卖出成交金额,万元为单位。',
  },
  {
    key: 'reason',
    name: '上榜原因',
    tag: 'lhb',
    brief: '触发龙虎榜披露的规则,常见的有:涨跌幅偏离 ±7%、振幅 ±15%、换手率 20%、收盘涨跌 10%/15% 等。',
  },

  // =============== 评分维度 ===============
  {
    key: 'score',
    name: '综合评分',
    aliases: ['score', '评分'],
    tag: 'score',
    brief: '系统根据多个维度给每条龙虎榜记录打的 0-100 分,越高越值得关注。',
    detail: '权重大致:净买入额 30% + 游资强度 25% + 龙头属性 20% + 历史胜率 15% + 次日 / 5 日收益 10%。',
  },
  {
    key: 'hot_money_net',
    name: '游资净买入',
    tag: 'score',
    brief: '该股票当日所有游资席位的净买入合计。高意味着游资抱团。',
  },
  {
    key: 'win_rate',
    name: '胜率',
    aliases: ['历史胜率'],
    tag: 'score',
    brief: '该席位过去一段时间上榜后,**次日收盘上涨**的概率。',
    example: '某一线游资席位近 60 日胜率 65%,意味着它上榜的股票 10 只有 6~7 只次日会涨。',
  },
  {
    key: 't1_pnl_pct',
    name: 'T+1 收益',
    aliases: ['次日收益'],
    tag: 'score',
    brief: '龙虎榜披露后次日的涨跌幅(%)。正数表示跟买赚钱。',
  },
  {
    key: 't5_pnl_pct',
    name: 'T+5 收益',
    aliases: ['5 日收益'],
    tag: 'score',
    brief: '龙虎榜披露后第 5 个交易日的累计涨跌幅(%)。用于评估短线持有效果。',
  },
  {
    key: 'turnover_rate',
    name: '换手率',
    tag: 'market',
    brief: '当日成交股数 / 流通股本 × 100%。反映**筹码活跃度**。',
    example: '换手率 30% 意味着全天有 30% 的流通盘完成交易。一般 > 10% 算高度活跃。',
  },
  {
    key: 'amplitude',
    name: '振幅',
    tag: 'market',
    brief: '(当日最高价 − 最低价) / 昨收 × 100%。反映日内**波动大小**。',
  },

  // =============== 市场情绪 ===============
  {
    key: 'limit_up',
    name: '涨停',
    aliases: ['涨停板'],
    tag: 'sentiment',
    brief: 'A 股主板单日最多涨 10%(ST 股 5%,创业板/科创板 20%),到价后买盘封住无法交易。',
  },
  {
    key: 'limit_down',
    name: '跌停',
    tag: 'sentiment',
    brief: '单日最多跌 10%(ST 股 5%,创业板/科创板 20%),到价后卖盘封住。',
  },
  {
    key: 'cont_limit_up',
    name: '连板',
    aliases: ['连续涨停'],
    tag: 'sentiment',
    brief: '一只股票连续多日涨停。N 连板就是连续 N 天涨停,是市场情绪最热的标志。',
    example: '「5 连板」 = 连续 5 个交易日每天涨停。',
  },
  {
    key: 'first_limit_up',
    name: '首板',
    tag: 'sentiment',
    brief: '今日刚涨停,昨日未涨停的股票。是新热点诞生的标志。',
  },
  {
    key: 'up_down_ratio',
    name: '涨跌比',
    tag: 'sentiment',
    brief: '当日上涨家数 / 下跌家数。> 1 表示赚钱效应好,< 1 表示亏钱。',
    example: '涨跌比 3.5 表示上涨股是下跌股的 3.5 倍,典型的普涨日。',
  },
  {
    key: 'avg_pct_change',
    name: '平均涨幅',
    tag: 'sentiment',
    brief: '全市场所有股票当日涨跌幅的算术平均。代表「持有随机一只股票」的期望收益。',
  },
  {
    key: 'median_pct_change',
    name: '中位涨幅',
    tag: 'sentiment',
    brief: '全市场股票涨跌幅从小到大排序后**居中那只**的值。比平均更抗极端值,更能代表**大多数人**的真实感受。',
  },
  {
    key: 'strong_stock_count',
    name: '强势股',
    tag: 'sentiment',
    brief: '当日涨幅 > 5% 的股票数。',
  },
  {
    key: 'weak_stock_count',
    name: '弱势股',
    tag: 'sentiment',
    brief: '当日跌幅 > 5% 的股票数。',
  },
  {
    key: 'sentiment_score',
    name: '情绪评分',
    tag: 'sentiment',
    brief: '系统根据涨跌比、涨停家数、平均涨幅等综合算出的市场温度,0-100 分。',
    detail: '>=70 热(HOT),55-70 暖(WARM),45-55 中性(NEUTRAL),30-45 冷(COOL),<30 极冷(COLD)。',
  },

  // =============== 回测 ===============
  {
    key: 'sharpe',
    name: '夏普比率',
    aliases: ['Sharpe'],
    tag: 'backtest',
    brief: '「每承担 1 单位风险能赚多少超额收益」。衡量策略的**性价比**,越高越好。',
    detail: '> 1 算优秀,> 2 罕见。= (年化收益 − 无风险利率) / 年化波动率。',
  },
  {
    key: 'max_drawdown',
    name: '最大回撤',
    aliases: ['MDD'],
    tag: 'backtest',
    brief: '策略运行期间,净值从峰值跌到谷底的**最大跌幅**。衡量最坏情况。',
    example: '初始 100 万,最高涨到 180 万,最低又跌到 126 万,最大回撤 = (180-126)/180 = 30%。',
  },
  {
    key: 'annualized',
    name: '年化收益',
    tag: 'backtest',
    brief: '把任意长度的收益**折算成一整年的等效收益率**,方便横向对比。',
  },
  {
    key: 'win_rate_bt',
    name: '交易胜率',
    tag: 'backtest',
    brief: '回测中盈利的交易占总交易数的比例。> 50% 代表整体赚多亏少。',
  },
]

// 快速查找 map,避免每次 find 遍历
const GLOSSARY_INDEX: Map<string, Term> = (() => {
  const m = new Map<string, Term>()
  for (const t of GLOSSARY) {
    m.set(t.key.toLowerCase(), t)
    m.set(t.name, t)
    for (const a of t.aliases ?? []) m.set(a.toLowerCase(), t)
  }
  return m
})()

/** 按 key / 中文名 / 别名 查找术语,找不到返回 null。 */
export function findTerm(keyOrName: string): Term | null {
  if (!keyOrName) return null
  return (
    GLOSSARY_INDEX.get(keyOrName) ??
    GLOSSARY_INDEX.get(keyOrName.toLowerCase()) ??
    null
  )
}

/** 按 tag 筛选(常用于构建「术语表」页面)。 */
export function termsByTag(tag: Term['tag']): Term[] {
  return GLOSSARY.filter((t) => t.tag === tag)
}

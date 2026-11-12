export interface Story {
  id: string;
  recordedAt: string;
  category: string;
  industries: string[];
  importance: number;
  title: string;
  day: string;
  time?: string;
  source: string;
  url: string;
  paragraphs: string[];
  readingNote: string;
}

// The releases are historical. Discovery times and classification are prototype fixtures.
export const stories: Story[] = [
  {
    id: 'cn-gdp',
    recordedAt: '2024-10-18T10:06',
    category: '宏观经济',
    industries: [],
    importance: 3,
    day: '2024-10-18',
    time: '10:00',
    source: '国家统计局',
    title: '中国前三季度 GDP 同比增长 4.8%',
    url: 'https://www.stats.gov.cn/sj/zxfb/202410/t20241018_1957044.html',
    paragraphs: [
      '国家统计局初步核算，2024 年前三季度国内生产总值为 949746 亿元。按不变价格计算，比上年同期增长 4.8%。',
      '分产业看，第一产业同比增长 3.4%，第二产业增长 5.4%，第三产业增长 4.7%。单看第三季度，国内生产总值同比增长 4.6%。',
    ],
    readingNote:
      '4.8% 对应前三个季度的累计同比增速，4.6% 对应第三季度单季。同一份公告里，这两个数字的统计区间不同。',
  },
  {
    id: 'cn-industry',
    recordedAt: '2024-10-18T10:04',
    category: '未分类',
    industries: [],
    importance: 1,
    day: '2024-10-18',
    time: '10:00',
    source: '国家统计局',
    title: '9 月规模以上工业增加值同比增长 5.4%',
    url: 'https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202410/t20241018_1957043.html',
    paragraphs: [
      '2024 年 9 月，规模以上工业增加值同比实际增长 5.4%，增速比 8 月提高 0.9 个百分点。',
      '公布的增加值增速已扣除价格因素。当月规模以上工业企业产品销售率为 96.0%。',
    ],
    readingNote: '增加值增速衡量扣除价格因素后的变化，销售率衡量产品销售情况，两个指标的分母不同。',
  },
  {
    id: 'cn-retail',
    recordedAt: '2024-10-18T10:05',
    category: '未分类',
    industries: ['消费'],
    importance: 1,
    day: '2024-10-18',
    time: '10:00',
    source: '国家统计局',
    title: '9 月社会消费品零售总额同比增长 3.2%，增速较上月加快',
    url: 'https://www.stats.gov.cn/sj/zxfb/202410/t20241018_1957039.html',
    paragraphs: [
      '9 月社会消费品零售总额为 41112 亿元，同比增长 3.2%。当月同比增速比 8 月提高 1.1 个百分点。',
      '从前三季度累计看，实物商品网上零售额同比增长 7.9%，占同期社会消费品零售总额的 25.7%。',
    ],
    readingNote:
      '3.2% 描述 9 月全部社会消费品零售额，7.9% 描述前三季度网上实物商品零售额。比较时要同时核对范围和期间。',
  },
  {
    id: 'us-industry',
    recordedAt: '2024-10-18T08:02',
    category: '货币与流动性',
    industries: ['能源'],
    importance: 2,
    day: '2024-10-17',
    time: '21:15',
    source: '美联储',
    title: '美国 9 月工业产出环比下降 0.3%',
    url: 'https://www.federalreserve.gov/Releases/G17/20241017/g17.pdf',
    paragraphs: [
      '美联储公布的工业生产数据显示，9 月美国工业产出较上月下降 0.3%，8 月则环比上升 0.3%。',
      '该统计覆盖制造业、采矿业以及电力和燃气公用事业。公告同时提供各行业的产出和产能利用情况。',
    ],
    readingNote: '工业产出的覆盖范围比制造业更广。核对制造业活动时，可在公告中单独查看制造业分项。',
  },
  {
    id: 'ecb-rates',
    recordedAt: '2024-10-17T21:02',
    category: '货币与流动性',
    industries: [],
    importance: 2,
    day: '2024-10-17',
    source: '欧洲央行',
    title: '欧洲央行下调三项关键利率，存款便利利率降至 3.25%',
    url: 'https://www.ecb.europa.eu/press/pr/date/2024/html/ecb.mp241017~aa366eaf20.en.html',
    paragraphs: [
      '欧洲央行管理委员会决定将三项关键利率各下调 25 个基点，新利率自 10 月 23 日起生效。',
      '调整后，存款便利利率为 3.25%，主要再融资操作利率为 3.40%，边际贷款便利利率为 3.65%。',
    ],
    readingNote: '10 月 17 日是决议公布日，10 月 23 日是生效日。查看资金成本时需要区分这两个日期。',
  },
  {
    id: 'cn-cpi',
    recordedAt: '2024-10-13T09:34',
    category: '宏观经济',
    industries: ['消费', '医药医疗'],
    importance: 3,
    day: '2024-10-13',
    time: '09:30',
    source: '国家统计局',
    title: '中国 9 月居民消费价格同比上涨 0.4%',
    url: 'https://www.stats.gov.cn/sj/zxfb/202410/t20241013_1956899.html',
    paragraphs: [
      '9 月全国居民消费价格同比上涨 0.4%，与 8 月相比持平。该指数跟踪城乡居民购买的消费品和服务价格。',
      '调查范围包括食品烟酒、衣着、居住、交通通信、医疗保健等八大类。',
    ],
    readingNote: '同比的基准是上年同月，环比的基准是上月，因此同比上涨和环比持平可以同时出现。',
  },
  {
    id: 'cn-ppi',
    recordedAt: '2024-10-13T09:32',
    category: '宏观经济',
    industries: [],
    importance: 2,
    day: '2024-10-13',
    time: '09:30',
    source: '国家统计局',
    title: '中国 9 月工业生产者出厂价格同比下降 2.8%',
    url: 'https://www.stats.gov.cn/sj/zxfb/202410/t20241013_1956898.html',
    paragraphs: [
      '国家统计局公布，9 月工业生产者出厂价格较上年同月下降 2.8%，较上月下降 0.6%。',
      '同期工业生产者购进价格同比下降 2.2%，环比下降 0.8%。',
    ],
    readingNote: '出厂价格和购进价格分别对应企业销售产品和购买投入品两个环节。',
  },
  {
    id: 'us-ppi',
    recordedAt: '2024-10-11T20:38',
    category: '宏观经济',
    industries: [],
    importance: 2,
    day: '2024-10-11',
    time: '20:30',
    source: '美国劳工统计局',
    title: '美国 9 月最终需求生产者价格环比持平',
    url: 'https://www.bls.gov/news.release/archives/ppi_10112024.htm',
    paragraphs: [
      '美国劳工统计局报告，季节调整后的最终需求生产者价格指数在 9 月与 8 月持平。未经季节调整的同比涨幅为 1.8%。',
      '服务价格环比上涨 0.2%，商品价格下降 0.2%，两者对总指数的影响相互抵消。',
    ],
    readingNote: '总指数持平时，各分项仍会变化。此处商品与服务的价格方向恰好相反。',
  },
  {
    id: 'us-cpi',
    recordedAt: '2024-10-11T12:01',
    category: '宏观经济',
    industries: ['消费'],
    importance: 3,
    day: '2024-10-10',
    time: '20:30',
    source: '美国劳工统计局',
    title: '美国 9 月 CPI 同比上涨 2.4%，核心 CPI 同比上涨 3.3%',
    url: 'https://www.bls.gov/news.release/archives/cpi_10102024.htm',
    paragraphs: [
      '美国城市消费者价格指数 9 月同比上涨 2.4%，经季节调整后环比上涨 0.2%。',
      '扣除食品和能源后，核心指数同比上涨 3.3%。同期能源价格指数比一年前低 6.8%。',
    ],
    readingNote: '核心指数剔除食品与能源。两个同比读数有差异时，需要结合被剔除的分项判断。',
  },
  {
    id: 'fed-minutes',
    recordedAt: '2024-10-10T02:08',
    category: '货币与流动性',
    industries: [],
    importance: 2,
    day: '2024-10-10',
    time: '02:00',
    source: '美联储',
    title: '美联储公布 9 月议息会议纪要',
    url: 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20241009a.htm',
    paragraphs: [
      '美联储于美东时间 10 月 9 日下午公布 9 月 17 日至 18 日的联邦公开市场委员会会议纪要，北京时间为 10 月 10 日凌晨。',
      '纪要记录的是与会者开会时可获得的经济与金融信息，公开页面提供完整文本与 PDF。',
    ],
    readingNote: '会议日期早于纪要公开日期。将纪要与当日市场信息对照时，应使用其实际公开时点。',
  },
  {
    id: 'us-jobs',
    recordedAt: '2024-10-04T20:31',
    category: '宏观经济',
    industries: ['医药医疗'],
    importance: 3,
    day: '2024-10-04',
    time: '20:30',
    source: '美国劳工统计局',
    title: '美国 9 月非农就业人数初值增加 25.4 万',
    url: 'https://www.bls.gov/news.release/archives/empsit_10042024.htm',
    paragraphs: [
      '按 10 月 4 日首次公布的数据，美国 9 月非农就业人数增加 25.4 万人，失业率为 4.1%。',
      '就业增长涉及餐饮服务、医疗保健、政府、社会救助和建筑等行业。',
    ],
    readingNote: '标题采用首次公布的数值。对照后续月份的报告时，需要核对其中对历史月份的修订。',
  },
];

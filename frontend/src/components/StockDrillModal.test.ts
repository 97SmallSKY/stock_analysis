import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import StockDrillModal from './StockDrillModal.vue'
import { useStockDrill } from '@/composables/useStockDrill'

vi.mock('@/api/kline', () => ({
  fetchKlineDaily: vi.fn(async () => ({ rows: [], empty_reason: 'out_of_range' })),
  fetchKlineWeekly: vi.fn(async () => ({ rows: [], empty_reason: 'out_of_range' })),
  fetchKlineMinute: vi.fn(async () => ({ rows: [], empty_reason: 'no_source_table' })),
}))

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn(),
    on: vi.fn(), off: vi.fn(),
  })),
}))
vi.mock('echarts/charts', () => ({ CandlestickChart: {}, LineChart: {}, BarChart: {} }))
vi.mock('echarts/components', () => ({
  GridComponent: {}, LegendComponent: {}, TooltipComponent: {}, TitleComponent: {},
  DataZoomComponent: {}, MarkLineComponent: {}, MarkPointComponent: {},
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

describe('StockDrillModal 分钟K 空态', () => {
  beforeEach(() => {
    const d = useStockDrill()
    d.close()
    d.state.tsCode = null
  })

  it('分钟 K 无数据时显示大号 无', async () => {
    const drill = useStockDrill()
    drill.open('000001.SZ')

    const wrapper = mount(StockDrillModal, {
      global: {
        stubs: {
          'a-modal': { template: '<div><slot name="title"/><slot/></div>' },
          'a-tabs': { template: '<div><slot/></div>' },
          'a-tab-pane': { template: '<div><slot/></div>' },
          'a-breadcrumb': { template: '<div><slot/></div>' },
          'a-breadcrumb-item': { template: '<div><slot/></div>' },
          'a-tag': { template: '<span><slot/></span>' },
          'a-button': { template: '<button><slot/></button>' },
          'a-spin': { template: '<div><slot/></div>' },
          'a-empty': { template: '<div><slot name="description"/><slot/></div>' },
        },
      },
    })
    await flushPromises()

    // 切到 minute,模拟已选日期
    const vm = wrapper.vm as unknown as {
      tab: string
      pickedDate: string | null
      load: () => Promise<void>
    }
    vm.pickedDate = '2024-05-20'
    vm.tab = 'minute'
    await vm.load()
    await flushPromises()

    const el = wrapper.find('[data-test="minute-empty"]')
    expect(el.exists()).toBe(true)
    expect(el.find('.big-none').text()).toBe('无')
  })

  it('daily 空态不显示大号 无, 走 a-empty 分支', async () => {
    const drill = useStockDrill()
    drill.open('000001.SZ')

    const wrapper = mount(StockDrillModal, {
      global: {
        stubs: {
          'a-modal': { template: '<div><slot name="title"/><slot/></div>' },
          'a-tabs': { template: '<div><slot/></div>' },
          'a-tab-pane': { template: '<div><slot/></div>' },
          'a-breadcrumb': { template: '<div><slot/></div>' },
          'a-breadcrumb-item': { template: '<div><slot/></div>' },
          'a-tag': { template: '<span><slot/></span>' },
          'a-button': { template: '<button><slot/></button>' },
          'a-spin': { template: '<div><slot/></div>' },
          'a-empty': { template: '<div class="a-empty-stub"><slot name="description"/><slot/></div>' },
        },
      },
    })
    await flushPromises()
    const vm = wrapper.vm as unknown as { tab: string; load: () => Promise<void> }
    vm.tab = 'daily'
    await vm.load()
    await flushPromises()

    expect(wrapper.find('[data-test="minute-empty"]').exists()).toBe(false)
    expect(wrapper.find('.a-empty-stub').exists()).toBe(true)
  })
})

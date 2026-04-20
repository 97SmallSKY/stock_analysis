// STO-46: UX 修复验证 —— tooltip 提示缺失原因 / alert 提示已选策略
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import Backtest from './Backtest.vue'

vi.mock('@/api/backtest', () => ({
  listStrategies: vi.fn(async () => ({ strategies: ['Alpha', 'Beta', 'Gamma'] })),
  compareBacktests: vi.fn(async () => ({
    start_date: '2024-01-01',
    end_date: '2024-12-31',
    winner: 'Alpha#1',
    runs: [{ name: 'Alpha#1', ok: true }],
  })),
  fetchBacktestHistory: vi.fn(async () => ({ rows: [] })),
}))

vi.mock('@/components/DateRangeQuickPick.vue', () => ({
  default: {
    name: 'DateRangeQuickPick',
    props: ['modelValue'],
    template: '<div class="mock-date-range"/>',
  },
}))

const commonStubs = {
  'a-tabs': { template: '<div><slot/></div>' },
  'a-tab-pane': { template: '<div><slot/></div>' },
  'a-card': { template: '<div><slot name="extra"/><slot/></div>' },
  'a-row': { template: '<div><slot/></div>' },
  'a-col': { template: '<div><slot/></div>' },
  'a-form-item': { template: '<div><slot/></div>' },
  'a-input-number': { template: '<input/>' },
  'a-select': { template: '<div><slot/></div>' },
  'a-space': { template: '<div><slot/></div>' },
  'a-table': { template: '<div/>' },
  'a-tag': { template: '<span><slot/></span>' },
  'a-alert': {
    props: ['message', 'type'],
    template: '<div class="mock-alert" :data-type="type">{{ message }}</div>',
  },
  'a-tooltip': {
    props: ['title'],
    template: '<div class="mock-tooltip" :data-title="title"><slot/></div>',
  },
  'a-button': {
    props: ['disabled', 'loading', 'type'],
    template:
      '<button class="mock-btn" :disabled="disabled" :data-loading="loading"><slot name="icon"/><slot/></button>',
  },
  PlayCircleOutlined: { template: '<i/>' },
}

type VM = {
  range: { start: string; end: string }
  pickedStrategies: string[]
  canRun: boolean
  disabledReason: string
  showRunHint: boolean
  runHintMsg: string
}

async function mountPage() {
  const wrapper = mount(Backtest, { global: { stubs: commonStubs } })
  await flushPromises()
  return wrapper
}

describe('Backtest UX (STO-46)', () => {
  it('无日期无策略时:按钮 disabled,tooltip 提示「请先选择日期范围」', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as unknown as VM

    // 模拟日期被清空
    vm.range.start = ''
    vm.range.end = ''
    vm.pickedStrategies = []
    await nextTick()

    expect(vm.canRun).toBe(false)
    expect(vm.disabledReason).toBe('请先选择日期范围')

    const tip = wrapper.find('.mock-tooltip')
    expect(tip.exists()).toBe(true)
    expect(tip.attributes('data-title')).toBe('请先选择日期范围')
  })

  it('有日期但未勾策略:tooltip 提示「请至少勾选 1 个策略」', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as unknown as VM

    vm.range.start = '2024-01-01'
    vm.range.end = '2024-12-31'
    vm.pickedStrategies = []
    await nextTick()

    expect(vm.canRun).toBe(false)
    expect(vm.disabledReason).toBe('请至少勾选 1 个策略')
    expect(vm.showRunHint).toBe(false) // 还没勾不该出 alert
    expect(wrapper.find('.mock-alert').exists()).toBe(false)
  })

  it('勾选策略 + 有日期:Alert 可见,按钮 enabled 无 tooltip', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as unknown as VM

    vm.range.start = '2024-01-01'
    vm.range.end = '2024-12-31'
    vm.pickedStrategies = ['Alpha', 'Beta']
    await nextTick()

    expect(vm.canRun).toBe(true)
    expect(vm.disabledReason).toBe('')
    expect(vm.showRunHint).toBe(true)
    expect(vm.runHintMsg).toContain('已选 2 个策略')

    const alert = wrapper.find('.mock-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.attributes('data-type')).toBe('info')
    expect(alert.text()).toContain('已选 2 个策略')

    // canRun 时不渲染 tooltip 包装
    expect(wrapper.find('.mock-tooltip').exists()).toBe(false)
  })
})

/**
 * STO-89 / STO-80b: BacktestRun 表单页单测。
 *
 * 覆盖 AC (d):≥ 6 case
 * - 表单校验:空策略/日期格式/起止反向/初始资金过低
 * - 提交成功:message.success 调用 + lastSubmit 赋值 + API payload 正确
 * - 提交失败:message.error 调用
 *
 * 策略:mock @/api/backtest + 用 defineExpose 暴露 form/canSubmit/onSubmit
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BacktestRun from './BacktestRun.vue'

const submitMock = vi.fn()
vi.mock('@/api/backtest', () => ({
  listStrategies: vi.fn(async () => ({
    strategies: [
      { name: 'lhb_hot_money', description: '龙虎榜游资', category: 'LHB', default_params: {} },
      { name: 'momentum', description: '动量', category: 'TREND', default_params: {} },
    ],
  })),
  submitBacktest: (req: unknown) => submitMock(req),
}))

const messageSuccess = vi.fn()
const messageError = vi.fn()
vi.mock('ant-design-vue', () => ({
  message: {
    success: (...a: unknown[]) => messageSuccess(...a),
    error: (...a: unknown[]) => messageError(...a),
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const stubs = {
  'a-card': { template: '<div><slot name="title"/><slot name="extra"/><slot/></div>' },
  'a-form': { template: '<form><slot/></form>' },
  'a-form-item': { template: '<div><slot/></div>' },
  'a-input': {
    props: ['value'], emits: ['update:value'],
    template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)"/>',
  },
  'a-input-number': {
    props: ['value'], emits: ['update:value'],
    template: '<input type="number" :value="value" @input="$emit(\'update:value\', Number($event.target.value))"/>',
  },
  'a-select': {
    props: ['value'], emits: ['update:value'],
    template: '<select :value="value" @change="$emit(\'update:value\', $event.target.value)"><option value="lhb_hot_money">lhb</option><option value="momentum">m</option></select>',
  },
  'a-button': {
    props: ['disabled', 'loading'],
    template: '<button :disabled="disabled" :data-loading="loading" @click="$emit(\'click\')"><slot/></button>',
  },
  'a-space': { template: '<div><slot/></div>' },
  'a-typography-text': { template: '<span><slot/></span>' },
  'a-alert': {
    props: ['message'],
    template: '<div class="alert">{{ message }}<slot name="description"/></div>',
  },
}

type VM = {
  form: {
    strategy_name: string
    start_date: string
    end_date: string
    initial_capital: number
    max_positions: number
    per_position_pct: number
  }
  canSubmit: boolean
  disabledReason: string
  onSubmit: () => Promise<void>
  lastSubmit: { task_id: string; status: string } | null
}

async function mountPage() {
  const wrapper = mount(BacktestRun, { global: { stubs, mocks: { $router: { push: vi.fn() } } } })
  await flushPromises()
  return wrapper
}

describe('BacktestRun 表单校验', () => {
  beforeEach(() => {
    submitMock.mockReset()
    messageSuccess.mockReset()
    messageError.mockReset()
  })

  it('初始状态 canSubmit=false 且提示「请选择策略」', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    expect(vm.canSubmit).toBe(false)
    expect(vm.disabledReason).toBe('请选择策略')
  })

  it('日期格式错误 → 提示「格式需为 YYYY-MM-DD」', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'lhb_hot_money'
    vm.form.start_date = '2026/01/01'
    vm.form.end_date = '2026-12-31'
    await flushPromises()
    expect(vm.canSubmit).toBe(false)
    expect(vm.disabledReason).toContain('YYYY-MM-DD')
  })

  it('起始日期晚于结束日期 → canSubmit=false', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'momentum'
    vm.form.start_date = '2026-12-31'
    vm.form.end_date = '2026-01-01'
    await flushPromises()
    expect(vm.canSubmit).toBe(false)
    expect(vm.disabledReason).toContain('晚于')
  })

  it('初始资金 < 10000 → canSubmit=false', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'lhb_hot_money'
    vm.form.start_date = '2026-01-01'
    vm.form.end_date = '2026-06-30'
    vm.form.initial_capital = 500
    await flushPromises()
    expect(vm.canSubmit).toBe(false)
    expect(vm.disabledReason).toContain('初始资金')
  })

  it('合法表单 canSubmit=true 且 disabledReason 为空', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'lhb_hot_money'
    vm.form.start_date = '2026-01-01'
    vm.form.end_date = '2026-06-30'
    vm.form.initial_capital = 1_000_000
    await flushPromises()
    expect(vm.canSubmit).toBe(true)
    expect(vm.disabledReason).toBe('')
  })

  it('提交成功:调 API + message.success + lastSubmit 赋值', async () => {
    submitMock.mockResolvedValue({ task_id: 'abc123', status: 'PENDING' })
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'lhb_hot_money'
    vm.form.start_date = '2026-01-01'
    vm.form.end_date = '2026-06-30'
    vm.form.initial_capital = 1_000_000
    vm.form.max_positions = 5
    vm.form.per_position_pct = 0.2
    await flushPromises()
    await vm.onSubmit()
    expect(submitMock).toHaveBeenCalledOnce()
    const payload = submitMock.mock.calls[0][0]
    expect(payload.strategy_name).toBe('lhb_hot_money')
    expect(payload.start_date).toBe('2026-01-01')
    expect(payload.end_date).toBe('2026-06-30')
    expect(payload.initial_capital).toBe(1_000_000)
    expect(messageSuccess).toHaveBeenCalledOnce()
    expect(vm.lastSubmit?.task_id).toBe('abc123')
  })

  it('提交失败:message.error 被调,lastSubmit 保持 null', async () => {
    submitMock.mockRejectedValue(new Error('API 500'))
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    vm.form.strategy_name = 'momentum'
    vm.form.start_date = '2026-01-01'
    vm.form.end_date = '2026-06-30'
    await flushPromises()
    await vm.onSubmit()
    expect(messageError).toHaveBeenCalledOnce()
    expect(vm.lastSubmit).toBeNull()
  })

  it('canSubmit=false 时 onSubmit 提前退出不调 API', async () => {
    const w = await mountPage()
    const vm = w.vm as unknown as VM
    // 未填表单
    await vm.onSubmit()
    expect(submitMock).not.toHaveBeenCalled()
  })
})

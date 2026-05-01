<template>
  <div class="backtest-run-page">
    <a-card :bordered="false">
      <template #title>
        <span>回测任务 · 提交新任务</span>
      </template>
      <template #extra>
        <a-button @click="router.push('/backtest-history')">查看历史任务 →</a-button>
      </template>

      <a-form :label-col="{ span: 5 }" :wrapper-col="{ span: 16 }" class="backtest-submit-form">
        <a-form-item label="策略" required>
          <a-select
            v-model:value="form.strategy_name"
            placeholder="请选择已注册策略"
            :options="strategyOptions"
            show-search
            :filter-option="(input: string, option: any) => option.label.toLowerCase().includes(input.toLowerCase())"
          />
          <div v-if="selectedStrategy" class="strategy-desc">
            {{ selectedStrategy.description || selectedStrategy.name }}
          </div>
          <div class="strategy-stock-hint">
            <a-typography-text type="secondary" style="font-size:12px;">
              💡 龙虎榜策略通过信号自动选股（全市场龙虎榜标的），无需指定股票代码
            </a-typography-text>
          </div>
        </a-form-item>

        <a-form-item label="起始日期" required>
          <a-date-picker
            :value="startDateDayjs"
            @change="(_: unknown, s: string) => { form.start_date = s }"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
            style="width: 240px;"
            :disabled-date="isWeekend"
          />
        </a-form-item>

        <a-form-item label="结束日期" required>
          <a-date-picker
            :value="endDateDayjs"
            @change="(_: unknown, s: string) => { form.end_date = s }"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
            style="width: 240px;"
            :disabled-date="isWeekend"
          />
        </a-form-item>

        <a-form-item label="初始资金(元)">
          <a-input-number
            v-model:value="form.initial_capital"
            :min="10000"
            :step="100000"
            style="width: 240px;"
          />
        </a-form-item>

        <a-form-item label="最大持仓数">
          <a-input-number v-model:value="form.max_positions" :min="1" :max="50" style="width: 120px;" />
        </a-form-item>

        <a-form-item label="单仓比例">
          <a-input-number
            v-model:value="form.per_position_pct"
            :min="0.01"
            :max="1.0"
            :step="0.05"
            :precision="2"
            style="width: 120px;"
          />
        </a-form-item>

        <a-form-item :wrapper-col="{ offset: 5 }">
          <a-space>
            <a-button
              type="primary"
              :loading="submitting"
              :disabled="!canSubmit"
              @click="onSubmit"
            >
              提交回测
            </a-button>
            <a-typography-text v-if="disabledReason" type="secondary">
              {{ disabledReason }}
            </a-typography-text>
          </a-space>
        </a-form-item>
      </a-form>

      <a-alert
        v-if="lastSubmit"
        type="success"
        :message="`已提交 task_id=${lastSubmit.task_id} (status=${lastSubmit.status})`"
        show-icon
        closable
        style="margin-top: 16px;"
      >
        <template #description>
          <a @click="router.push('/backtest-history')">前往历史页查看进度 →</a>
        </template>
      </a-alert>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import {
  listStrategies, submitBacktest,
  type SubmitBacktestResp, type StrategyInfo,
} from '@/api/backtest'

const router = useRouter()
const strategyOptions = ref<{ value: string; label: string }[]>([])
const strategies = ref<StrategyInfo[]>([])
const submitting = ref(false)
const lastSubmit = ref<SubmitBacktestResp | null>(null)

// STO-128: 最近交易日（跳过周末，不处理节假日）
function latestTradingDay(d: Dayjs = dayjs()): Dayjs {
  const dow = d.day() // 0=Sun, 6=Sat
  if (dow === 0) return d.subtract(2, 'day')
  if (dow === 6) return d.subtract(1, 'day')
  return d
}

// STO-128: 禁用周末
function isWeekend(d: Dayjs): boolean {
  return d.day() === 0 || d.day() === 6
}

const _end = latestTradingDay()
const _start = latestTradingDay(_end.subtract(1, 'year'))

const form = reactive({
  strategy_name: '' as string,
  start_date: _start.format('YYYY-MM-DD') as string,
  end_date: _end.format('YYYY-MM-DD') as string,
  initial_capital: 1_000_000,
  max_positions: 5,
  per_position_pct: 0.2,
})

// DatePicker 绑定需要 dayjs 对象
const startDateDayjs = computed(() => form.start_date ? dayjs(form.start_date) : null)
const endDateDayjs = computed(() => form.end_date ? dayjs(form.end_date) : null)

// STO-89 (d): 表单校验规则
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

const selectedStrategy = computed(() =>
  strategies.value.find((s) => s.name === form.strategy_name) ?? null
)

const canSubmit = computed(() =>
  !!form.strategy_name
  && DATE_RE.test(form.start_date)
  && DATE_RE.test(form.end_date)
  && form.start_date <= form.end_date
  && (form.initial_capital ?? 0) >= 10000,
)

const disabledReason = computed(() => {
  if (!form.strategy_name) return '请选择策略'
  if (!DATE_RE.test(form.start_date)) return '起始日期格式需为 YYYY-MM-DD'
  if (!DATE_RE.test(form.end_date)) return '结束日期格式需为 YYYY-MM-DD'
  if (form.start_date > form.end_date) return '起始日期不得晚于结束日期'
  if ((form.initial_capital ?? 0) < 10000) return '初始资金不能小于 10000'
  return ''
})

async function onSubmit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const resp = await submitBacktest({
      strategy_name: form.strategy_name,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_capital: form.initial_capital,
      max_positions: form.max_positions,
      per_position_pct: form.per_position_pct,
    })
    lastSubmit.value = resp
    message.success('回测任务已提交,执行中…')
  } catch (e: unknown) {
    const msg = (e as { message?: string })?.message ?? String(e)
    message.error(`提交失败: ${msg}`)
  } finally {
    submitting.value = false
  }
}

async function refreshStrategies() {
  try {
    const r = await listStrategies()
    strategies.value = r.strategies || []
    strategyOptions.value = strategies.value.map((s) => ({
      value: s.name,
      label: s.description || s.name,
    }))
  } catch { /* ignore */ }
}

onMounted(refreshStrategies)

// 测试导出
defineExpose({ form, canSubmit, disabledReason, onSubmit, lastSubmit, strategies, selectedStrategy })
</script>

<style scoped>
.backtest-run-page { padding: 12px; }
.backtest-submit-form { max-width: 680px; }
.strategy-desc {
  margin-top: 4px;
  font-size: 12px;
  color: #8c8c8c;
  line-height: 1.4;
}
</style>

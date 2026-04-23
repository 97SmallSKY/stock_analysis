/**
 * STO-62: 前端 limit 参数边界常量
 *
 * 与后端 `backend/api/{lhb,kline}.py` 的 `Query(..., le=X)` 一一对应。
 * 修改后端 le= 时必须同步此文件(见项目 CLAUDE.md §11)。
 *
 * 使用方式:fetchXxx 已内置校验,消费方只需用常量而非硬编码数字。
 */

export const LIMIT_MAX_LHB = 1000      // backend/api/lhb.py::list_lhb   le=1000
export const LIMIT_MAX_LHB_SEATS = 2000 // backend/api/lhb.py::list_seats le=2000
export const LIMIT_MAX_KLINE_DAILY = 5000  // backend/api/kline.py::daily  le=5000
export const LIMIT_MAX_KLINE_WEEKLY = 2000 // backend/api/kline.py::weekly le=2000

export class LimitOutOfRangeError extends Error {
  constructor(name: string, limit: number, max: number) {
    super(`${name} limit=${limit} exceeds max=${max} (see backend le= constraint)`)
    this.name = 'LimitOutOfRangeError'
  }
}

export function assertLimit(name: string, limit: number | undefined, max: number): void {
  if (limit === undefined) return
  if (!Number.isFinite(limit) || limit < 1) {
    throw new LimitOutOfRangeError(name, limit, max)
  }
  if (limit > max) {
    throw new LimitOutOfRangeError(name, limit, max)
  }
}

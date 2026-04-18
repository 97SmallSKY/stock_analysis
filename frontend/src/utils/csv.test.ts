import { describe, it, expect, beforeEach, vi } from 'vitest'
import { rowsToCsv, downloadCsv } from './csv'

describe('rowsToCsv', () => {
  it('空数组返回空串', () => {
    expect(rowsToCsv([])).toBe('')
  })

  it('基本字段 + 自动取列名', () => {
    const csv = rowsToCsv([
      { a: 1, b: 'x' },
      { a: 2, b: 'y' },
    ])
    expect(csv.split('\n')).toEqual(['a,b', '1,x', '2,y'])
  })

  it('指定列序', () => {
    const csv = rowsToCsv(
      [{ a: 1, b: 2, c: 3 }],
      ['c', 'a'],
    )
    expect(csv).toBe('c,a\n3,1')
  })

  it('转义: 逗号/引号/换行 -> 包双引号; 内部 " 翻倍', () => {
    const csv = rowsToCsv([
      { x: 'a,b', y: 'he said "hi"', z: 'line1\nline2' },
    ])
    expect(csv).toBe('x,y,z\n"a,b","he said ""hi""","line1\nline2"')
  })

  it('null/undefined 渲染为空串', () => {
    const csv = rowsToCsv([
      { a: null, b: undefined, c: 0 },
    ])
    expect(csv).toBe('a,b,c\n,,0')
  })
})

describe('downloadCsv', () => {
  let createUrl: any
  let revokeUrl: any
  let click: any

  beforeEach(() => {
    createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob://x')
    revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    click = vi.fn()
    // 让 createElement('a') 返回带 click spy 的对象
    const originalCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: any) => {
      if (tag === 'a') {
        const a = originalCreate('a') as HTMLAnchorElement
        ;(a as any).click = click
        return a
      }
      return originalCreate(tag)
    })
  })

  it('构造 blob url 并点击下载', () => {
    downloadCsv('x.csv', 'a,b\n1,2')
    expect(createUrl).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(revokeUrl).toHaveBeenCalledOnce()
  })
})

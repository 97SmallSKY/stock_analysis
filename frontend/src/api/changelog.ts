// 「特性介绍」/ 开发日志 API client
// 后端:backend/api/changelog.py,数据源为 docs/CHANGELOG.md

import http from './http'

export type ChangelogType = 'feature' | 'fix' | 'refactor' | 'perf' | 'docs' | 'ops' | string

export interface ChangelogEntry {
  date: string       // YYYY-MM-DD
  type: ChangelogType
  title: string
  body: string       // 原始 markdown(页面端用简易渲染)
}

export interface ChangelogResp {
  entries: ChangelogEntry[]
  types: ChangelogType[]
  total: number
  error?: string
}

export async function fetchChangelog(): Promise<ChangelogResp> {
  const { data } = await http.get<ChangelogResp>('/api/changelog')
  return data
}

export const CHANGELOG_TYPE_META: Record<string, { label: string; color: string }> = {
  feature:  { label: '新特性',  color: 'geekblue' },
  fix:      { label: '修复',    color: 'red' },
  refactor: { label: '重构',    color: 'purple' },
  perf:     { label: '性能',    color: 'green' },
  docs:     { label: '文档',    color: 'default' },
  ops:      { label: '运维',    color: 'orange' },
}

// 「特性介绍」/「开发日志」API client
// 后端:backend/api/changelog.py,数据源:
//   changelog (特性) -> docs/CHANGELOG.md
//   devlog    (开发) -> docs/DEVLOG.md

import http from './http'

export type ChangelogType =
  | 'feature' | 'fix' | 'ui'                          // CHANGELOG 用
  | 'refactor' | 'perf' | 'devops' | 'docs'           // DEVLOG 用
  | string

export interface ChangelogEntry {
  date: string       // YYYY-MM-DD
  type: ChangelogType
  title: string
  body: string       // 原始 markdown(页面端用简易渲染)
}

export interface ChangelogResp {
  source?: 'changelog' | 'devlog'
  entries: ChangelogEntry[]
  types: ChangelogType[]
  total: number
  error?: string
}

export async function fetchChangelog(): Promise<ChangelogResp> {
  const { data } = await http.get<ChangelogResp>('/api/changelog')
  return data
}

// P20.1: 新增开发日志接口
export async function fetchDevlog(): Promise<ChangelogResp> {
  const { data } = await http.get<ChangelogResp>('/api/devlog')
  return data
}

export const CHANGELOG_TYPE_META: Record<string, { label: string; color: string }> = {
  // CHANGELOG 类型
  feature:  { label: '新特性',  color: 'geekblue' },
  fix:      { label: '修复',    color: 'red' },
  ui:       { label: 'UI 调整', color: 'purple' },
  // DEVLOG 类型
  refactor: { label: '重构',    color: 'purple' },
  perf:     { label: '性能',    color: 'green' },
  devops:   { label: '运维',    color: 'orange' },
  docs:     { label: '文档',    color: 'default' },
  ops:      { label: '运维',    color: 'orange' },   // 兼容旧别名
}

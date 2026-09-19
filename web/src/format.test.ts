import { fmtBytes, fmtDuration, fmtEta, fmtUploadDate } from './format'

test('formats durations', () => {
  expect(fmtDuration(19)).toBe('0:19')
  expect(fmtDuration(3725)).toBe('1:02:05')
  expect(fmtDuration(null)).toBe('–')
})
test('formats bytes, eta and dates', () => {
  expect(fmtBytes(1536)).toBe('1.5 KB')
  expect(fmtBytes(30 * 1024 ** 3)).toBe('30.0 GB')
  expect(fmtEta(125)).toBe('2m 5s left')
  expect(fmtEta(NaN)).toBe('')
  expect(fmtUploadDate('20260919')).toBe('2026-09-19')
})

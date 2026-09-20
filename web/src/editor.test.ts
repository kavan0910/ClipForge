import { activeWord, addExclude, outToSrc, removeExclude, srcToOut, type EditorWord, type Segment } from './editor'

const segs: Segment[] = [
  { src_in: 10, src_out: 15, out_in: 0 },
  { src_in: 20, src_out: 30, out_in: 5 },
]

test('maps source to output through the cuts and back', () => {
  expect(srcToOut(segs, 12)).toBe(2)
  expect(srcToOut(segs, 17)).toBeNull() // inside the removed range
  expect(srcToOut(segs, 25)).toBe(10)
  expect(outToSrc(segs, 2)).toBe(12)
  expect(outToSrc(segs, 7)).toBe(22)
  expect(outToSrc(segs, 999)).toBe(30) // clamped to the end
  for (const t of [10, 13.7, 22, 29.5]) expect(outToSrc(segs, srcToOut(segs, t)!)).toBeCloseTo(t)
})

test('finds the active kept word', () => {
  const words: EditorWord[] = [
    { i: 0, w: 'a', start: 10, end: 11, kept: true, speaker: null },
    { i: 1, w: 'b', start: 16, end: 17, kept: false, speaker: null },
    { i: 2, w: 'c', start: 21, end: 22, kept: true, speaker: null },
  ]
  expect(activeWord(words, segs, 0.5)).toBe(0)
  expect(activeWord(words, segs, 6.5)).toBe(2)
  expect(activeWord(words, segs, 4)).toBe(0) // the removed word is skipped
})

test('exclusion ranges merge, extend and split', () => {
  expect(addExclude([], 5, 8)).toEqual([[5, 8]])
  expect(addExclude([[5, 8]], 9, 12)).toEqual([[5, 12]]) // adjacent
  expect(addExclude([[5, 8], [20, 25]], 7, 21)).toEqual([[5, 25]]) // bridges two
  expect(addExclude([[5, 8]], 12, 10)).toEqual([[5, 8], [10, 12]]) // reversed selection
  expect(removeExclude([[5, 25]], 10, 12)).toEqual([[5, 9], [13, 25]])
  expect(removeExclude([[5, 8]], 0, 100)).toEqual([])
})

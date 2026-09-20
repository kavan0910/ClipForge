// Types mirror backend/clipforge/captions (spec.py, timeline.py). The JSON Schema for the template
// is exported to spec/template.schema.json; keep these in step with it.
export interface Font { file: string; family: string; weight: number; italic: boolean }
export interface Stroke { color: string; width: number }
export interface Shadow { color: string; blur: number; dx: number; dy: number; opacity: number }
export interface Box { color: string; opacity: number; radius: number; pad_x: number; pad_y: number }
export interface Active { mode: 'none' | 'color' | 'scale' | 'pill' | 'underline'; color: string; scale: number; pill_color: string; pill_radius: number; underline_color: string; underline_height: number }
export interface Emphasis { color: string | null; scale: number; weight: number | null }
export interface Motion { kind: 'none' | 'pop' | 'fade' | 'slide_up' | 'typewriter'; ms: number; easing: 'linear' | 'ease_out' | 'ease_in_out' | 'back_out'; out_kind: 'none' | 'fade'; out_ms: number }
export interface HookStyle { font: Font | null; size: number; case: 'none' | 'upper' | 'lower'; fill: string; stroke: Stroke; box: Box | null; y: number; seconds: number; max_width: number }
export interface Template {
  id: string; label: string; font: Font; size: number; case: 'none' | 'upper' | 'lower'; tracking: number; line_height: number
  fill: string; stroke: Stroke; shadow: Shadow; box: Box | null; active: Active; emphasis: Emphasis; motion: Motion
  anchor: { y: number; align: string }; hook: HookStyle
}
export interface TWord { w: string; start: number; end: number; emphasis: boolean; src_i: number }
export interface Chunk { id: number; start: number; end: number; words: TWord[]; lines: number[][]; scale: number; script: string; font: Font | null; emoji: string | null }
export interface HookBlock { text: string; lines: string[]; start: number; end: number; scale: number }
export interface Timeline { template_id: string; width: number; height: number; duration: number; chunks: Chunk[]; hook: HookBlock | null; safe: Record<string, number> }
export interface Band { y0: number; h: number }
export interface CaptionProps { timeline: Timeline; template: Template; fontBase: string; layer?: 'all' | 'captions' | 'hook'; band?: Band; times?: number[] }

import { create } from 'zustand'

export interface OptimizationResult {
  success: boolean
  result_info: {
    objective_value?: number
    rotation_errors?: number
    rotation_cost?: number
    deficit_total_percent?: number
    deficit_cost?: number
    deficit_by_culture?: Record<string, { target_ha: number; deficit_percent: number; deficit_ha: number }>
    solve_time?: number
    status?: string
    // Балл факторов формирования СПП (docs/FACTORS_SPP.md). Все поля
    // опциональны: прогоны, сохранённые до внедрения модели, их не содержат.
    factor_weight?: number
    factor_authority?: number
    factor_weight_effective?: number
    two_stage?: boolean
    base_objective?: number | null
    factor_score_avg?: number | null
    factor_score_area_avg?: number | null
    factor_scored_fields?: number
    factor_regret_total?: number
    factor_cost?: number
    hard_blocked_pred_pairs?: number
    hard_block_pred_enabled?: boolean
  }
  factors_coverage?: {
    scoring_weight: number
    constraint_weight: number
    available_weight: number
    coverage: number
    sources_present: string[]
    factors: Array<{
      id: string
      name: string
      category: string
      role: string
      weight: number
      status: string
      fields_covered: number | null
      data_source: string
    }>
  } | null
  optimized_plan?: Array<{
    field_id: string
    year: number
    culture_unified: string
    area_ha: number
    farm?: string | null
    subdivision?: string | null
    predecessor?: string | null
    factor_score?: number | null
    factor_regret?: number | null
  }>
  original_violations?: Array<{
    field_id: string
    culture: string
    area_ha: number
    reason: string
  }>
  validation?: {
    is_valid: boolean
    errors: Array<Record<string, unknown>>
  }
  comparison?: Array<{
    culture: string
    target: number
    actual: number
    percent: number
  }>
  crop_targets?: Record<string, number>
  message?: string
  per_farm_info?: Record<string, {
    success: boolean
    info?: {
      status?: string
      solve_time?: number
      infeasible_reason?: string
    }
  }>
}

/** Контекст запуска: для какого хозяйства/года строился план + уникальный id. */
export interface RunMeta {
  runId: string
  runAt: number // Date.now()
  planningYear: number
  farms: string[] // хозяйства, для которых строился план ([] = все хозяйства)
}

interface ResultsState {
  result: OptimizationResult | null
  runMeta: RunMeta | null
  isRunning: boolean
  statusMessage: string
  setResult: (result: OptimizationResult) => void
  setRunMeta: (meta: RunMeta) => void
  setRunning: (running: boolean) => void
  setStatusMessage: (msg: string) => void
  clear: () => void
}

export const useResultsStore = create<ResultsState>((set) => ({
  result: null,
  runMeta: null,
  isRunning: false,
  statusMessage: '',
  setResult: (result) => set({ result, isRunning: false }),
  setRunMeta: (runMeta) => set({ runMeta }),
  setRunning: (isRunning) => set({ isRunning }),
  setStatusMessage: (statusMessage) => set({ statusMessage }),
  clear: () => set({ result: null, runMeta: null, isRunning: false, statusMessage: '' }),
}))

import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

interface FieldRecord {
  field_id: string
  farm: string | null
  subdivision: string | null
  culture: string | null
  area_ha: number | null
  fertility_score: number | null
  is_perennial: boolean
}

interface ChemicalRecord {
  year: number
  substances: string[]
}

interface Summary {
  total_fields: number
  total_area: number
  optimizable_fields: number
  optimizable_area: number
}

export function useFields(
  year = 2026,
  farm?: string,
  subdivision?: string,
  culture?: string,
  options?: { enabled?: boolean },
) {
  const params = new URLSearchParams({ year: String(year) })
  if (farm) params.set('farm', farm)
  if (subdivision) params.set('subdivision', subdivision)
  if (culture) params.set('culture', culture)

  return useQuery<FieldRecord[]>({
    queryKey: ['fields', year, farm, subdivision, culture],
    queryFn: () => api.get(`/data/fields?${params}`),
    enabled: options?.enabled ?? true,
  })
}

/** Поля хозяйства из актуального снимка (последний год с данными ≤ planning_year),
 *  как их видит солвер. Для списка исключения полей. */
export function useFarmFields(farm: string | undefined, planningYear: number) {
  return useQuery<FieldRecord[]>({
    queryKey: ['farm-fields', farm, planningYear],
    queryFn: () =>
      api.get(
        `/data/farm-fields?farm=${encodeURIComponent(farm!)}&planning_year=${planningYear}`,
      ),
    enabled: !!farm,
  })
}

export function useChemicalHistory(fieldId: string | null) {
  return useQuery<ChemicalRecord[]>({
    queryKey: ['chemical-history', fieldId],
    queryFn: () => api.get(`/data/fields/${fieldId}/chemical-history`),
    enabled: !!fieldId,
  })
}

export function useCultures(year = 2026, farm?: string) {
  const params = new URLSearchParams({ year: String(year) })
  if (farm) params.set('farm', farm)

  return useQuery<string[]>({
    queryKey: ['cultures', year, farm],
    queryFn: () => api.get(`/data/cultures?${params}`),
  })
}

/** Все уникальные оптимизируемые культуры (без фильтра по году). */
export function useAllOptimizableCultures() {
  return useQuery<string[]>({
    queryKey: ['cultures-all-optimizable'],
    queryFn: () => api.get('/data/cultures?optimizable_only=true'),
  })
}

export interface CropColor {
  color: string
  matched: boolean
  matched_by: string | null
  canonical: string | null
  short_name: string | null
  category: string | null
  sub_category: string | null
  code_1c: string | null
}

export interface CropColorsResponse {
  fallback: string
  reference_size: number
  matched: number
  total: number
  colors: Record<string, CropColor>
}

/** Палитра культур из справочника заказчика (crop_colors.csv). Справочник за
 *  сессию не меняется — держим бессрочно. */
export function useCropColors(year?: number) {
  const params = year ? `?year=${year}` : ''
  return useQuery<CropColorsResponse>({
    queryKey: ['crop-colors', year],
    queryFn: () => api.get(`/data/crop-colors${params}`),
    staleTime: Infinity,
  })
}

export interface FactorBand {
  order: number
  texture: string
  range: string
  lo: number | null
  hi: number | null
  zone: string
  k: number
  score: number
}

export interface FactorRecord {
  id: string
  order: number
  category: string
  name: string
  data_type: string
  role: 'hard_blocker' | 'bonus' | 'modifier' | 'objective' | 'constraint'
  role_raw: string
  weight: number
  norm_kind: string
  unit: string
  data_source: string
  description: string
  notes: string
  bands: FactorBand[]
  enum_values: Array<{ value: string; k: number; maps_to: string | null; note: string }>
}

export interface FactorsResponse {
  total_weight: number
  count: number
  factors: FactorRecord[]
}

/** Реестр факторов формирования СПП (24 фактора, сумма весов 100). */
export function useFactors() {
  return useQuery<FactorsResponse>({
    queryKey: ['factors'],
    queryFn: () => api.get('/factors'),
    staleTime: Infinity,
  })
}

export interface FactorCoverageEntry {
  id: string
  name: string
  category: string
  role: string
  weight: number
  norm_kind: string
  unit: string
  data_source: string
  description: string
  status: 'available' | 'no_data' | 'implemented' | 'not_implemented'
  fields_covered: number | null
}

export interface FactorsCoverage {
  planning_year: number
  total_weight: number
  scoring_weight: number
  constraint_weight: number
  available_weight: number
  coverage: number
  sources_present: string[]
  factors: FactorCoverageEntry[]
}

/** Какие факторы реально обеспечены данными. */
export function useFactorsCoverage(planningYear: number) {
  return useQuery<FactorsCoverage>({
    queryKey: ['factors-coverage', planningYear],
    queryFn: () => api.get(`/factors/coverage?planning_year=${planningYear}`),
  })
}

/** Нецелевые категории землепользования (Пар, Строительство, …) для ручного
 *  исключения полей из планирования. */
export function useNonTargetCategories() {
  return useQuery<string[]>({
    queryKey: ['non-target-categories'],
    queryFn: () => api.get('/data/non-target-categories'),
  })
}

export function useFarms(year?: number) {
  const params = year ? `?year=${year}` : ''
  return useQuery<string[]>({
    queryKey: ['farms', year],
    queryFn: () => api.get(`/data/farms${params}`),
  })
}

export function useYears() {
  return useQuery<number[]>({
    queryKey: ['years'],
    queryFn: () => api.get('/data/years'),
  })
}

export function useSubdivisions(farm?: string) {
  const params = farm ? `?farm=${encodeURIComponent(farm)}` : ''
  return useQuery<string[]>({
    queryKey: ['subdivisions', farm],
    queryFn: () => api.get(`/data/subdivisions${params}`),
  })
}

export function useCropTargets(year = 2026) {
  return useQuery<Record<string, number>>({
    queryKey: ['crop-targets', year],
    queryFn: () => api.get(`/data/crop-targets?year=${year}`),
  })
}

export type PerFarmTargets = Record<string, Record<string, number>>

export function useCropTargetsPerFarm(year = 2026) {
  return useQuery<PerFarmTargets>({
    queryKey: ['crop-targets-per-farm', year],
    queryFn: () => api.get(`/data/crop-targets?year=${year}&per_farm=true`),
  })
}

export interface PlanFeatureCollection {
  type: 'FeatureCollection'
  source?: string
  features: Array<{
    type: 'Feature'
    geometry: any
    properties: {
      field_id: string
      culture: string | null
      area_ha: number
      farm: string | null
      subdivision: string | null
    }
  }>
  n_features?: number
  message?: string
}

export function usePlanGeoJson(source: 'plan' | 'history' = 'plan', planningYear = 2026) {
  return useQuery<PlanFeatureCollection>({
    queryKey: ['plan-geojson', source, planningYear],
    queryFn: () =>
      api.get(`/optimization/plan-geojson?source=${source}&planning_year=${planningYear}`),
  })
}

export interface SavedResultMeta {
  run_id: string
  created_at: string
  farms: string[]
  planning_year: number
  status?: string
  objective_value?: number
  deficit_total_percent?: number
  n_fields?: number
  /** Балл факторов СПП, взвешенный по гектарам. Нет у прогонов до внедрения модели. */
  factor_score_area_avg?: number | null
}

/** Список сохранённых результатов: scope='recent' (за час) | 'all'. */
export function useSavedResults(scope: 'recent' | 'all') {
  return useQuery<SavedResultMeta[]>({
    queryKey: ['saved-results', scope],
    queryFn: () => api.get(`/optimization/results?scope=${scope}`),
    staleTime: 0,
  })
}

export function useSummary(year = 2026, farm?: string) {
  const params = new URLSearchParams({ year: String(year) })
  if (farm) params.set('farm', farm)

  return useQuery<Summary>({
    queryKey: ['summary', year, farm],
    queryFn: () => api.get(`/data/summary?${params}`),
  })
}

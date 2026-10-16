import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  useCropTargetsPerFarm,
  useYears,
  useAllOptimizableCultures,
  useFarmFields,
  useNonTargetCategories,
} from '@/hooks/useData'
import { useTargetsStore } from '@/stores/targets'
import { useOptimizationRun } from '@/hooks/useOptimizationRun'
import { PLANNING_YEAR } from '@/config'
import MetricCard from '@/components/shared/MetricCard'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import { Play, Plus, RotateCcw, X } from 'lucide-react'

/**
 * Планирование для ОДНОГО хозяйства за раз.
 * Резко уменьшает размер задачи и время решения (секунды вместо минут).
 */
export default function SinglePlanPage() {
  const navigate = useNavigate()
  const { data: years } = useYears()
  const {
    planningYear,
    setPlanningYear,
    hardRatio,
    softRatio,
    weightAdjacency,
    weightFactorScore,
    setHardRatio,
    setSoftRatio,
    setWeightAdjacency,
    setWeightFactorScore,
  } = useTargetsStore()

  // Дефолт года = max(years)
  useEffect(() => {
    if (planningYear === null && years && years.length > 0) {
      setPlanningYear(PLANNING_YEAR)
    }
  }, [planningYear, years, setPlanningYear])

  const effectiveYear = planningYear ?? 2026
  const { data: allFarmTargets, isLoading } = useCropTargetsPerFarm(effectiveYear)

  const farms = useMemo(
    () => (allFarmTargets ? Object.keys(allFarmTargets).sort() : []),
    [allFarmTargets],
  )
  const [selectedFarm, setSelectedFarm] = useState<string>('')

  // Когда подтянулся список ферм — выбираем первую
  useEffect(() => {
    if (!selectedFarm && farms.length > 0) {
      setSelectedFarm(farms[0])
    }
  }, [farms, selectedFarm])

  // Локальные цели именно для этой страницы (не трогаем глобальный store
  // чтобы не путать с «все хозяйства»)
  const [farmCultures, setFarmCultures] = useState<Record<string, number>>({})

  // При смене фермы или загрузке дефолтов — перезаливаем таблицу
  useEffect(() => {
    if (selectedFarm && allFarmTargets?.[selectedFarm]) {
      setFarmCultures({ ...allFarmTargets[selectedFarm] })
    }
  }, [selectedFarm, allFarmTargets])

  const sortedCultures = useMemo(
    () =>
      Object.entries(farmCultures).sort(([, a], [, b]) => {
        // Незаполненные (0 га) — наверх, чтобы только что добавленную культуру
        // было видно сразу и можно было задать ей площадь (иначе она тонет внизу).
        if ((a === 0) !== (b === 0)) return a === 0 ? -1 : 1
        return b - a
      }),
    [farmCultures],
  )
  const totalArea = useMemo(
    () => Object.values(farmCultures).reduce((s, v) => s + v, 0),
    [farmCultures],
  )

  const updateCulture = (culture: string, area: number) =>
    setFarmCultures((prev) => ({ ...prev, [culture]: area }))

  const removeCulture = (culture: string) =>
    setFarmCultures((prev) => {
      const { [culture]: _, ...rest } = prev
      return rest
    })

  // Список доступных культур для добавления (все - уже добавленные)
  const { data: allCultures } = useAllOptimizableCultures()
  const [showAddPicker, setShowAddPicker] = useState(false)
  const cultureToAdd = useMemo(
    () =>
      (allCultures ?? []).filter((c) => !(c in farmCultures)),
    [allCultures, farmCultures],
  )
  const addCulture = (culture: string) => {
    setFarmCultures((prev) => ({ ...prev, [culture]: 0 }))
    setShowAddPicker(false)
  }

  // === Исключение полей из планирования ===
  // Поля берём из актуального снимка хозяйства (последний год с данными
  // ≤ года планирования) — той же логикой, что и солвер. У части хозяйств в
  // самом свежем глобальном году данных нет (напр. «Защитное Юг» — до 2025),
  // поэтому фиксированный год вернул бы пусто. Запрос идёт только когда выбрано
  // хозяйство (иначе вернулись бы все ~8500 полей → 100% CPU при рендере).
  const { data: farmFields } = useFarmFields(selectedFarm || undefined, effectiveYear)
  const { data: nonTargetCategories } = useNonTargetCategories()
  const defaultCategory = nonTargetCategories?.[0] ?? 'Пар'

  // field_id -> нецелевая категория. Эти поля выпадают из солвера.
  const [excludedFields, setExcludedFields] = useState<Record<string, string>>({})
  const [fieldSearch, setFieldSearch] = useState('')

  // Сбрасываем исключения при смене хозяйства
  useEffect(() => {
    setExcludedFields({})
    setFieldSearch('')
  }, [selectedFarm])

  const toggleFieldExcluded = (fieldId: string, on: boolean) =>
    setExcludedFields((prev) => {
      if (on) return { ...prev, [fieldId]: prev[fieldId] ?? defaultCategory }
      const { [fieldId]: _, ...rest } = prev
      return rest
    })
  const setFieldCategory = (fieldId: string, category: string) =>
    setExcludedFields((prev) => ({ ...prev, [fieldId]: category }))

  const filteredFields = useMemo(() => {
    const q = fieldSearch.trim().toLowerCase()
    const list = farmFields ?? []
    if (!q) return list
    return list.filter((f) =>
      [f.field_id, f.subdivision, f.culture]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    )
  }, [farmFields, fieldSearch])

  // Ограничиваем число строк в DOM (большие хозяйства — сотни полей), чтобы не
  // тормозить рендер. Остальное доступно через поиск.
  const MAX_VISIBLE_FIELDS = 200
  const visibleFields = useMemo(
    () => filteredFields.slice(0, MAX_VISIBLE_FIELDS),
    [filteredFields],
  )

  const { run, isRunning, statusMessage } = useOptimizationRun()

  const handleRun = async () => {
    if (!selectedFarm) return
    const ok = await run({
      cropTargetsOverride: { [selectedFarm]: farmCultures },
      planningYearOverride: effectiveYear,
      fieldOverridesOverride: excludedFields,
    })
    if (ok) navigate('/results')
  }

  if (isLoading) return <LoadingSpinner />

  const yearOptions = (() => {
    const set = new Set<number>([PLANNING_YEAR, ...(years ?? [])])
    if (set.size === 0) set.add(effectiveYear)
    return Array.from(set).sort((a, b) => b - a)
  })()

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-stone-900">Планирование одного хозяйства</h2>
          <p className="mt-1 text-sm text-stone-500">
            Быстрый расчёт для отдельного хозяйства. Решение находится за секунды
            (вместо минут для всех хозяйств сразу) и редко упирается в тайм-аут.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <div>
            <label className="block text-xs font-medium text-stone-500">Хозяйство</label>
            <select
              value={selectedFarm}
              onChange={(e) => setSelectedFarm(e.target.value)}
              className="mt-1 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {farms.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-stone-500">Год планирования</label>
            <select
              value={effectiveYear}
              onChange={(e) => setPlanningYear(Number(e.target.value))}
              className="mt-1 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {yearOptions.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Table */}
        <div className="lg:col-span-2 space-y-3">
          <div className="rounded-xl border border-stone-200 bg-white">
            <div className="max-h-[500px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-stone-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-semibold text-stone-700">Культура</th>
                    <th className="px-4 py-3 text-right font-semibold text-stone-700">Площадь (га)</th>
                    <th className="w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100">
                  {sortedCultures.map(([culture, area]) => (
                    <tr key={culture} className="hover:bg-stone-50">
                      <td className="px-4 py-2.5 text-stone-800">{culture}</td>
                      <td className="px-4 py-2.5 text-right">
                        <input
                          type="number"
                          value={Math.round(area)}
                          onChange={(e) => updateCulture(culture, Number(e.target.value))}
                          min={0}
                          max={100000}
                          className="w-28 rounded-md border border-stone-300 px-2 py-1 text-right text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                        />
                      </td>
                      <td className="pr-3">
                        <button
                          onClick={() => removeCulture(culture)}
                          title="Убрать культуру из плана"
                          className="rounded p-1 text-stone-400 hover:bg-red-50 hover:text-red-600"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {sortedCultures.length === 0 && (
                    <tr><td colSpan={3} className="p-6 text-center text-stone-400">
                      План пуст — добавьте культуры кнопкой ниже
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Add culture */}
          {!showAddPicker ? (
            <button
              onClick={() => setShowAddPicker(true)}
              disabled={cultureToAdd.length === 0}
              className="flex items-center gap-2 rounded-lg border border-dashed border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-50"
            >
              <Plus className="h-4 w-4" />
              Добавить культуру в план
              {cultureToAdd.length > 0 && (
                <span className="ml-1 text-xs text-stone-400">({cultureToAdd.length} доступно)</span>
              )}
            </button>
          ) : (
            <div className="rounded-lg border border-stone-300 bg-white p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium text-stone-700">Выберите культуру</span>
                <button onClick={() => setShowAddPicker(false)}
                        className="rounded p-1 text-stone-400 hover:bg-stone-100">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="grid max-h-64 grid-cols-2 gap-1 overflow-y-auto sm:grid-cols-3">
                {cultureToAdd.map((c) => (
                  <button
                    key={c}
                    onClick={() => addCulture(c)}
                    className="rounded-md px-3 py-1.5 text-left text-sm text-stone-700 hover:bg-primary-50 hover:text-primary-700"
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Исключение полей из планирования */}
          <div className="mt-4 rounded-xl border border-stone-200 bg-white">
            <div className="flex items-center justify-between gap-2 border-b border-stone-100 px-4 py-3">
              <h3 className="text-sm font-semibold text-stone-800">
                Исключить поля из планирования
                {Object.keys(excludedFields).length > 0 && (
                  <span className="ml-2 rounded-full bg-primary-100 px-2 py-0.5 text-xs font-medium text-primary-700">
                    {Object.keys(excludedFields).length}
                  </span>
                )}
              </h3>
            </div>
            <div className="p-3">
              <input
                type="text"
                value={fieldSearch}
                onChange={(e) => setFieldSearch(e.target.value)}
                placeholder="Поиск по номеру поля, подразделению, культуре…"
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
              />
            </div>
            <div className="max-h-80 divide-y divide-stone-100 overflow-y-auto text-sm">
              {visibleFields.map((f) => {
                const excluded = f.field_id in excludedFields
                return (
                  <div key={f.field_id} className="flex items-center gap-3 px-4 py-2 hover:bg-stone-50">
                    <input
                      type="checkbox"
                      checked={excluded}
                      onChange={(e) => toggleFieldExcluded(f.field_id, e.target.checked)}
                      className="h-4 w-4 flex-shrink-0 accent-primary-600"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-stone-800" title={f.field_id}>{f.field_id}</div>
                      {(f.subdivision || f.culture || f.area_ha) && (
                        <div className="truncate text-xs text-stone-400">
                          {[
                            f.subdivision,
                            f.culture,
                            f.area_ha ? `${Math.round(f.area_ha)} га` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      )}
                    </div>
                    {excluded && (
                      <select
                        value={excludedFields[f.field_id]}
                        onChange={(e) => setFieldCategory(f.field_id, e.target.value)}
                        className="flex-shrink-0 rounded-md border border-stone-300 bg-white px-2 py-1 text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                      >
                        {(nonTargetCategories ?? [defaultCategory]).map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    )}
                  </div>
                )
              })}
              {filteredFields.length === 0 && (
                <div className="p-6 text-center text-stone-400">
                  {farmFields && farmFields.length > 0
                    ? 'Ничего не найдено'
                    : 'Нет полей для этого хозяйства'}
                </div>
              )}
              {filteredFields.length > visibleFields.length && (
                <div className="px-4 py-2 text-center text-xs text-stone-400">
                  Показаны первые {visibleFields.length} из {filteredFields.length} — уточните поиск
                </div>
              )}
            </div>
            <p className="border-t border-stone-100 px-4 py-2 text-xs text-stone-400">
              Отмеченные поля не участвуют в оптимизации, но попадут в итоговый план
              с выбранной категорией.
            </p>
          </div>
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-5">
            <h3 className="font-semibold text-stone-800">Параметры оптимизации</h3>

            <div>
              <label className="flex justify-between text-sm text-stone-600">
                <span>Жёсткий минимум</span>
                <span className="font-semibold text-stone-900">{Math.round(hardRatio * 100)}%</span>
              </label>
              <input
                type="range" min={50} max={100} step={5}
                value={hardRatio * 100}
                onChange={(e) => setHardRatio(Number(e.target.value) / 100)}
                className="mt-2 w-full accent-primary-600"
              />
            </div>

            <div>
              <label className="flex justify-between text-sm text-stone-600">
                <span>Мягкий целевой</span>
                <span className="font-semibold text-stone-900">{Math.round(softRatio * 100)}%</span>
              </label>
              <input
                type="range" min={hardRatio * 100} max={100} step={5}
                value={softRatio * 100}
                onChange={(e) => setSoftRatio(Number(e.target.value) / 100)}
                className="mt-2 w-full accent-primary-600"
              />
            </div>

            <div>
              <label className="flex items-center justify-between text-sm text-stone-600">
                <span>Гео-кучность</span>
                <input
                  type="checkbox"
                  checked={weightAdjacency > 0}
                  onChange={(e) => setWeightAdjacency(e.target.checked ? 2 : 0)}
                  className="h-4 w-4 accent-primary-600"
                />
              </label>
            </div>

            <div>
              <label className="flex justify-between text-sm text-stone-600">
                <span title="Вес балла факторов формирования СПП в целевой функции. 0 — балл только показывается, но на план не влияет.">
                  Балл факторов СПП
                </span>
                <span className="font-semibold text-stone-900">{weightFactorScore}</span>
              </label>
              <input
                type="range" min={0} max={9} step={1}
                value={weightFactorScore}
                onChange={(e) => setWeightFactorScore(Number(e.target.value))}
                className="mt-2 w-full accent-primary-600"
              />
              <p className="mt-1 text-xs text-stone-400">
                Выше 9 балл начинает перевешивать ошибки севооборота
              </p>
            </div>
          </div>

          <MetricCard title="Хозяйство" value={selectedFarm || '—'} />
          <MetricCard title="Культур в плане" value={sortedCultures.length} />
          <MetricCard
            title="Общая площадь, га"
            value={totalArea.toLocaleString('ru-RU', { maximumFractionDigits: 0 })}
          />

          <button
            onClick={handleRun}
            disabled={isRunning || !selectedFarm}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-6 py-3 text-base font-semibold text-white shadow-sm hover:bg-primary-700 disabled:opacity-50"
          >
            {isRunning ? (
              <>
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                Оптимизация...
              </>
            ) : (
              <>
                <Play className="h-5 w-5" />
                Запустить
              </>
            )}
          </button>
          {statusMessage && (
            <p className={'text-xs ' + (statusMessage.startsWith('Ошибка') ? 'text-red-600' : 'text-stone-500')}>
              {statusMessage}
            </p>
          )}

          <button
            onClick={() => allFarmTargets?.[selectedFarm] && setFarmCultures({ ...allFarmTargets[selectedFarm] })}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-stone-300 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-50"
          >
            <RotateCcw className="h-4 w-4" />
            Сбросить к исходным
          </button>
        </div>
      </div>
    </div>
  )
}

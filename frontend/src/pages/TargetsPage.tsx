import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCropTargetsPerFarm, useYears, useAllOptimizableCultures } from '@/hooks/useData'
import { useOptimizationRun } from '@/hooks/useOptimizationRun'
import { useTargetsStore } from '@/stores/targets'
import { PLANNING_YEAR } from '@/config'
import MetricCard from '@/components/shared/MetricCard'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { Play, Plus, RotateCcw, X } from 'lucide-react'

const COLORS = [
  '#16a34a', '#15803d', '#166534', '#22c55e', '#4ade80',
  '#86efac', '#0d9488', '#0891b2', '#2563eb', '#7c3aed',
  '#c026d3', '#e11d48', '#ea580c', '#d97706', '#65a30d',
  '#059669', '#0284c7', '#4f46e5', '#9333ea', '#db2777',
]

const SUMMARY = '__summary__'

export default function TargetsPage() {
  const navigate = useNavigate()
  const { data: years } = useYears()
  const {
    cropTargets,
    planningYear,
    setCropTargets,
    setPlanningYear,
    updateTarget,
    removeTarget,
    hardRatio,
    softRatio,
    weightAdjacency,
    setHardRatio,
    setSoftRatio,
    setWeightAdjacency,
  } = useTargetsStore()
  const { run: runOptimization, isRunning, statusMessage } = useOptimizationRun()

  const handleRunAndNavigate = async () => {
    const ok = await runOptimization()
    if (ok) navigate('/results')
  }

  const isError = statusMessage.toLowerCase().startsWith('ошибка')

  // Дефолт года = последний доступный + 1 (планирование на год вперёд)
  useEffect(() => {
    if (planningYear === null && years && years.length > 0) {
      setPlanningYear(PLANNING_YEAR)
    }
  }, [planningYear, years, setPlanningYear])

  const effectiveYear = planningYear ?? 2026
  const { data: defaults, isLoading } = useCropTargetsPerFarm(effectiveYear)
  const [selected, setSelected] = useState<string>(SUMMARY)

  // Подтягиваем дефолты при смене года или первой загрузке
  useEffect(() => {
    if (defaults && Object.keys(cropTargets).length === 0) {
      setCropTargets(defaults)
    }
  }, [defaults, cropTargets, setCropTargets])

  const farms = useMemo(() => Object.keys(cropTargets).sort(), [cropTargets])

  // Сводка: для каждого хозяйства — total area + n cultures
  const summaryRows = useMemo(
    () =>
      farms.map((farm) => {
        const t = cropTargets[farm] ?? {}
        const total = Object.values(t).reduce((s, v) => s + v, 0)
        return { farm, total, cultures: Object.keys(t).length }
      }).sort((a, b) => b.total - a.total),
    [farms, cropTargets],
  )

  const totalAllFarms = useMemo(
    () => summaryRows.reduce((s, r) => s + r.total, 0),
    [summaryRows],
  )

  const totalCultures = useMemo(
    () => new Set(farms.flatMap((f) => Object.keys(cropTargets[f] ?? {}))).size,
    [farms, cropTargets],
  )

  // Для выбранного хозяйства — отсортированный список культур
  const farmTargets = selected === SUMMARY ? {} : cropTargets[selected] ?? {}
  const sortedFarmTargets = useMemo(
    () =>
      Object.entries(farmTargets).sort(([, a], [, b]) => {
        // Незаполненные (0 га) — наверх, чтобы только что добавленную культуру
        // было видно сразу и можно было задать ей площадь (иначе она тонет внизу).
        if ((a === 0) !== (b === 0)) return a === 0 ? -1 : 1
        return b - a
      }),
    [farmTargets],
  )
  const farmTotal = useMemo(
    () => Object.values(farmTargets).reduce((s, v) => s + v, 0),
    [farmTargets],
  )

  // Add/remove culture
  const { data: allCultures } = useAllOptimizableCultures()
  const [showAddPicker, setShowAddPicker] = useState(false)
  const culturesToAdd = useMemo(
    () => (allCultures ?? []).filter((c) => !(c in farmTargets)),
    [allCultures, farmTargets],
  )

  const pieData = useMemo(
    () =>
      selected === SUMMARY
        ? summaryRows.map((r) => ({ name: r.farm, value: Math.round(r.total) }))
        : sortedFarmTargets.map(([name, value]) => ({ name, value: Math.round(value) })),
    [selected, summaryRows, sortedFarmTargets],
  )

  if (isLoading) return <LoadingSpinner />

  // Опции года: реальные годы данных + PLANNING_YEAR (если ещё не в них)
  const yearOptions = (() => {
    const set = new Set<number>([PLANNING_YEAR, ...(years ?? [])])
    if (set.size === 0) set.add(effectiveYear)
    return Array.from(set).sort((a, b) => b - a)
  })()

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-stone-900">Целевые площади по хозяйствам</h2>
          <p className="mt-1 text-sm text-stone-500">
            Цели задаются отдельно для каждого хозяйства. Сводка — общая картина,
            нажмите на пилюлю хозяйства, чтобы редактировать его таблицу культур.
          </p>
        </div>
        <div className="flex items-end gap-2">
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

      {/* Pills с хозяйствами */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setSelected(SUMMARY)}
          className={
            'rounded-full border px-4 py-1.5 text-sm font-medium transition-colors ' +
            (selected === SUMMARY
              ? 'border-primary-600 bg-primary-600 text-white'
              : 'border-stone-300 bg-white text-stone-700 hover:bg-stone-50')
          }
        >
          Сводка
        </button>
        {farms.map((farm) => (
          <button
            key={farm}
            onClick={() => setSelected(farm)}
            className={
              'rounded-full border px-4 py-1.5 text-sm font-medium transition-colors ' +
              (selected === farm
                ? 'border-primary-600 bg-primary-600 text-white'
                : 'border-stone-300 bg-white text-stone-700 hover:bg-stone-50')
            }
          >
            {farm}
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Table */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border border-stone-200 bg-white">
            <div className="max-h-[500px] overflow-y-auto">
              {selected === SUMMARY ? (
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-stone-50">
                    <tr>
                      <th className="px-4 py-3 text-left font-semibold text-stone-700">Хозяйство</th>
                      <th className="px-4 py-3 text-right font-semibold text-stone-700">Культур</th>
                      <th className="px-4 py-3 text-right font-semibold text-stone-700">Площадь (га)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {summaryRows.map((r) => (
                      <tr key={r.farm}
                          className="cursor-pointer hover:bg-stone-50"
                          onClick={() => setSelected(r.farm)}>
                        <td className="px-4 py-2.5 text-stone-800">{r.farm}</td>
                        <td className="px-4 py-2.5 text-right text-stone-600">{r.cultures}</td>
                        <td className="px-4 py-2.5 text-right font-semibold">
                          {r.total.toLocaleString('ru-RU', { maximumFractionDigits: 0 })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-stone-50">
                    <tr>
                      <th className="px-4 py-3 text-left font-semibold text-stone-700">Культура</th>
                      <th className="px-4 py-3 text-right font-semibold text-stone-700">Площадь (га)</th>
                      <th className="w-10"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {sortedFarmTargets.map(([culture, area]) => (
                      <tr key={culture} className="hover:bg-stone-50">
                        <td className="px-4 py-2.5 text-stone-800">{culture}</td>
                        <td className="px-4 py-2.5 text-right">
                          <input
                            type="number"
                            value={Math.round(area)}
                            onChange={(e) => updateTarget(selected, culture, Number(e.target.value))}
                            min={0}
                            max={100000}
                            className="w-28 rounded-md border border-stone-300 px-2 py-1 text-right text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                          />
                        </td>
                        <td className="pr-3">
                          <button
                            onClick={() => removeTarget(selected, culture)}
                            title="Убрать культуру из плана"
                            className="rounded p-1 text-stone-400 hover:bg-red-50 hover:text-red-600"
                          >
                            <X className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                    {sortedFarmTargets.length === 0 && (
                      <tr><td colSpan={3} className="p-6 text-center text-stone-400">
                        План пуст — добавьте культуры кнопкой ниже
                      </td></tr>
                    )}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Add culture — только для выбранного хозяйства */}
          {selected !== SUMMARY && (!showAddPicker ? (
            <button
              onClick={() => setShowAddPicker(true)}
              disabled={culturesToAdd.length === 0}
              className="mt-3 flex items-center gap-2 rounded-lg border border-dashed border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-50"
            >
              <Plus className="h-4 w-4" />
              Добавить культуру
              {culturesToAdd.length > 0 && (
                <span className="ml-1 text-xs text-stone-400">({culturesToAdd.length} доступно)</span>
              )}
            </button>
          ) : (
            <div className="mt-3 rounded-lg border border-stone-300 bg-white p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium text-stone-700">Выберите культуру</span>
                <button onClick={() => setShowAddPicker(false)}
                        className="rounded p-1 text-stone-400 hover:bg-stone-100">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="grid max-h-64 grid-cols-2 gap-1 overflow-y-auto sm:grid-cols-3">
                {culturesToAdd.map((c) => (
                  <button
                    key={c}
                    onClick={() => { updateTarget(selected, c, 0); setShowAddPicker(false) }}
                    className="rounded-md px-3 py-1.5 text-left text-sm text-stone-700 hover:bg-primary-50 hover:text-primary-700"
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Sidebar controls */}
        <div className="space-y-4">
          <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-5">
            <h3 className="font-semibold text-stone-800">Параметры оптимизации</h3>

            <div>
              <label className="flex justify-between text-sm text-stone-600">
                <span>Жёсткий минимум</span>
                <span className="font-semibold text-stone-900">{Math.round(hardRatio * 100)}%</span>
              </label>
              <input
                type="range"
                min={50}
                max={100}
                step={5}
                value={hardRatio * 100}
                onChange={(e) => setHardRatio(Number(e.target.value) / 100)}
                className="mt-2 w-full accent-primary-600"
              />
              <p className="mt-1 text-xs text-stone-400">Минимальный % от плана (нельзя нарушить)</p>
            </div>

            <div>
              <label className="flex justify-between text-sm text-stone-600">
                <span>Мягкий целевой</span>
                <span className="font-semibold text-stone-900">{Math.round(softRatio * 100)}%</span>
              </label>
              <input
                type="range"
                min={hardRatio * 100}
                max={100}
                step={5}
                value={softRatio * 100}
                onChange={(e) => setSoftRatio(Number(e.target.value) / 100)}
                className="mt-2 w-full accent-primary-600"
              />
              <p className="mt-1 text-xs text-stone-400">Целевой % от плана (штраф за недобор)</p>
            </div>

            <div>
              <label className="flex items-center justify-between text-sm text-stone-600">
                <span>Гео-кучность (соседние поля)</span>
                <input
                  type="checkbox"
                  checked={weightAdjacency > 0}
                  onChange={(e) => setWeightAdjacency(e.target.checked ? 2 : 0)}
                  className="h-4 w-4 accent-primary-600"
                />
              </label>
            </div>
          </div>

          <MetricCard
            title={selected === SUMMARY ? 'Всего хозяйств' : 'Культур в этом хозяйстве'}
            value={selected === SUMMARY ? farms.length : sortedFarmTargets.length}
          />
          <MetricCard
            title={selected === SUMMARY ? 'Культур уникальных' : 'Хозяйство выбрано'}
            value={selected === SUMMARY ? totalCultures : selected}
          />
          <MetricCard
            title={selected === SUMMARY ? 'Общая площадь, га' : 'Площадь хозяйства, га'}
            value={(selected === SUMMARY ? totalAllFarms : farmTotal).toLocaleString('ru-RU', {
              maximumFractionDigits: 0,
            })}
          />

          <div className="space-y-2">
            <button
              onClick={handleRunAndNavigate}
              disabled={isRunning}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary-600 px-6 py-3.5 text-base font-semibold text-white shadow-sm hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRunning ? (
                <>
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Оптимизация...
                </>
              ) : (
                <>
                  <Play className="h-5 w-5" />
                  Запустить оптимизацию
                </>
              )}
            </button>
            {statusMessage && (
              <p
                className={
                  'text-center text-xs ' +
                  (isError ? 'text-red-600' : 'text-stone-500')
                }
              >
                {statusMessage}
              </p>
            )}
          </div>

          <button
            onClick={() => defaults && setCropTargets(defaults)}
            disabled={isRunning}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-stone-300 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-50"
          >
            <RotateCcw className="h-4 w-4" />
            Сбросить к исходным
          </button>
        </div>
      </div>

      {/* Pie chart */}
      <div className="rounded-xl border border-stone-200 bg-white p-5">
        <h3 className="mb-4 font-semibold text-stone-800">
          {selected === SUMMARY
            ? 'Распределение площадей по хозяйствам'
            : `Распределение культур: ${selected}`}
        </h3>
        <ResponsiveContainer width="100%" height={400}>
          <PieChart>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              outerRadius={150}
              dataKey="value"
              label={({ name, percent }) =>
                percent > 0.03 ? `${name} (${(percent * 100).toFixed(0)}%)` : ''
              }
              labelLine={false}
            >
              {pieData.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip formatter={(value: number) => `${value.toLocaleString('ru-RU')} га`} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

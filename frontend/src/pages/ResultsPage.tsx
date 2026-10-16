import { useState, useMemo, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useTargetsStore } from '@/stores/targets'
import { useResultsStore, type RunMeta, type OptimizationResult } from '@/stores/results'
import { api } from '@/api/client'
import { useSavedResults, type SavedResultMeta } from '@/hooks/useData'
import MetricCard from '@/components/shared/MetricCard'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend, Cell,
} from 'recharts'
import {
  Download, AlertTriangle, CheckCircle, Eye, ChevronDown, Search, Image as ImageIcon,
} from 'lucide-react'

const WEIGHT_ROTATION_ERROR = 1000
const WEIGHT_AREA_DEFICIT = 1

export default function ResultsPage() {
  const { cropTargets, hardRatio, softRatio, weightAdjacency } =
    useTargetsStore()
  const { result, runMeta, setResult, setRunMeta } = useResultsStore()

  const [scope, setScope] = useState<'recent' | 'all'>('recent')
  const [listOpen, setListOpen] = useState(false)
  const [query, setQuery] = useState('')
  const { data: savedResults, isLoading: savedLoading } = useSavedResults(scope)

  const filteredSaved = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = savedResults ?? []
    if (!q) return list
    return list.filter((m) =>
      [farmLabel(metaToRunMeta(m)), fmtDateTime(Date.parse(m.created_at)), m.run_id, String(m.planning_year)]
        .some((v) => String(v).toLowerCase().includes(q)),
    )
  }, [savedResults, query])

  // Показать сохранённый результат прямо на странице
  const showSaved = async (id: string) => {
    const data = await api.get<{ meta: SavedResultMeta; result: OptimizationResult }>(
      `/optimization/results/${id}`,
    )
    setResult(data.result)
    setRunMeta(metaToRunMeta(data.meta))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // Скачать сохранённый результат в Excel
  const downloadSaved = async (meta: SavedResultMeta) => {
    const data = await api.get<{ meta: SavedResultMeta; result: OptimizationResult }>(
      `/optimization/results/${meta.run_id}`,
    )
    const r = data.result
    if (!r.optimized_plan || !r.crop_targets) return
    const blob = await api.postBlob('/export/excel', {
      optimized_plan: r.optimized_plan,
      crop_targets: r.crop_targets,
      deficit_info: r.result_info?.deficit_by_culture,
    })
    downloadBlob(blob, buildExportName(metaToRunMeta(meta), 'xlsx'))
  }

  const handleExportCSV = async () => {
    if (!result?.optimized_plan || !result.crop_targets) return
    const blob = await api.postBlob('/export/csv', {
      optimized_plan: result.optimized_plan,
      crop_targets: result.crop_targets,
      deficit_info: result.result_info.deficit_by_culture,
    })
    downloadBlob(blob, buildExportName(runMeta, 'csv'))
  }

  const handleExportExcel = async () => {
    if (!result?.optimized_plan || !result.crop_targets) return
    const blob = await api.postBlob('/export/excel', {
      optimized_plan: result.optimized_plan,
      crop_targets: result.crop_targets,
      deficit_info: result.result_info.deficit_by_culture,
    })
    downloadBlob(blob, buildExportName(runMeta, 'xlsx'))
  }

  // === Картинка плана (PNG, по мотивам exp2_plot_per_farm) ===
  // Хозяйства плана для селектора (если их несколько — можно выбрать одно).
  const planFarms = useMemo(() => {
    if (runMeta?.farms?.length) return runMeta.farms
    const set = new Set<string>()
    for (const r of result?.optimized_plan ?? []) if (r.farm) set.add(r.farm)
    return Array.from(set)
  }, [runMeta, result])

  const [imgFarm, setImgFarm] = useState<string>('') // '' = все хозяйства
  const [imgBlob, setImgBlob] = useState<Blob | null>(null)
  const [imgUrl, setImgUrl] = useState<string | null>(null)
  const [imgLoading, setImgLoading] = useState(false)
  const [imgError, setImgError] = useState<string | null>(null)

  // Перерисовываем картинку при смене результата/выбранного хозяйства.
  useEffect(() => {
    let cancelled = false
    let createdUrl: string | null = null
    const plan = result?.optimized_plan
    if (!result?.success || !plan || plan.length === 0) {
      setImgBlob(null); setImgUrl(null); setImgError(null)
      return
    }
    setImgLoading(true); setImgError(null)
    api.postBlob('/export/plan-image', {
      optimized_plan: plan,
      farm: imgFarm || null,
      planning_year: runMeta?.planningYear,
    })
      .then((blob) => {
        if (cancelled) return
        createdUrl = URL.createObjectURL(blob)
        setImgBlob(blob)
        setImgUrl(createdUrl)
      })
      .catch((e) => { if (!cancelled) setImgError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setImgLoading(false) })
    return () => { cancelled = true; if (createdUrl) URL.revokeObjectURL(createdUrl) }
  }, [result, imgFarm, runMeta])

  const handleDownloadImage = () => {
    if (imgBlob) downloadBlob(imgBlob, buildExportName(runMeta, 'png'))
  }

  const info = result?.result_info
  const coverage = result?.factors_coverage

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-stone-900">Результаты оптимизации</h2>

      {/* Контекст запуска: для какого хозяйства и какой план */}
      {runMeta && (
        <div className="rounded-xl border border-stone-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-1 text-sm">
            <span>
              <span className="text-stone-500">Хозяйство: </span>
              <b className="text-stone-900">{farmLabel(runMeta)}</b>
            </span>
            <span>
              <span className="text-stone-500">Год плана: </span>
              <b className="text-stone-900">{runMeta.planningYear}</b>
            </span>
            <span className="text-xs text-stone-400">
              ID запуска: {runMeta.runId} · {fmtDateTime(runMeta.runAt)}
            </span>
          </div>
        </div>
      )}

      {/* Parameters */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <MetricCard title="Жёсткий минимум" value={`${Math.round(hardRatio * 100)}%`} />
        <MetricCard title="Мягкий целевой" value={`${Math.round(softRatio * 100)}%`} />
        <MetricCard
          title="Хозяйств"
          value={(runMeta?.farms.length ?? Object.keys(cropTargets).length) || '—'}
        />
        <MetricCard title="Гео-кучность" value={weightAdjacency > 0 ? 'Вкл' : 'Выкл'} />
      </div>

      {/* Сохранённые результаты — выпадающий список с поиском */}
      <div className="rounded-xl border border-stone-200 bg-white">
        <button
          onClick={() => setListOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        >
          <span className="text-sm font-semibold text-stone-800">
            Сохранённые результаты
            {savedResults && savedResults.length > 0 && (
              <span className="ml-2 rounded-full bg-stone-100 px-2 py-0.5 text-xs font-medium text-stone-600">
                {savedResults.length}
              </span>
            )}
          </span>
          <ChevronDown
            className={`h-4 w-4 flex-shrink-0 text-stone-400 transition-transform ${listOpen ? 'rotate-180' : ''}`}
          />
        </button>

        {listOpen && (
          <div className="border-t border-stone-100">
            {/* Фильтры: период + поиск */}
            <div className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="flex gap-1 rounded-lg border border-stone-300 bg-white p-1">
                <button
                  onClick={() => setScope('recent')}
                  className={
                    'rounded-md px-3 py-1 text-sm font-medium transition-colors ' +
                    (scope === 'recent' ? 'bg-primary-600 text-white' : 'text-stone-700 hover:bg-stone-100')
                  }
                >
                  За час
                </button>
                <button
                  onClick={() => setScope('all')}
                  className={
                    'rounded-md px-3 py-1 text-sm font-medium transition-colors ' +
                    (scope === 'all' ? 'bg-primary-600 text-white' : 'text-stone-700 hover:bg-stone-100')
                  }
                >
                  Все
                </button>
              </div>
              <div className="relative min-w-[200px] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Поиск по хозяйству, дате, ID, году…"
                  className="w-full rounded-lg border border-stone-300 py-2 pl-8 pr-3 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                />
              </div>
            </div>

            {/* Скроллируемый список */}
            <div className="max-h-72 overflow-y-auto border-t border-stone-100">
              {savedLoading ? (
                <p className="p-4 text-sm text-stone-400">Загрузка…</p>
              ) : filteredSaved.length > 0 ? (
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-stone-50">
                    <tr>
                      <th className="px-4 py-2 text-left font-semibold text-stone-700">Хозяйство</th>
                      <th className="px-4 py-2 text-left font-semibold text-stone-700">Когда</th>
                      <th className="px-4 py-2 text-right font-semibold text-stone-700">Год</th>
                      <th className="px-4 py-2 text-right font-semibold text-stone-700">Полей</th>
                      <th className="px-4 py-2 text-right font-semibold text-stone-700">Дефицит</th>
                      <th className="w-44"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {filteredSaved.map((m) => (
                      <tr key={m.run_id} className="hover:bg-stone-50">
                        <td className="px-4 py-2 text-stone-800">{farmLabel(metaToRunMeta(m))}</td>
                        <td className="px-4 py-2 text-stone-500">{fmtDateTime(Date.parse(m.created_at))}</td>
                        <td className="px-4 py-2 text-right text-stone-600">{m.planning_year}</td>
                        <td className="px-4 py-2 text-right text-stone-600">{m.n_fields ?? '—'}</td>
                        <td className="px-4 py-2 text-right text-stone-600">
                          {m.deficit_total_percent != null ? `${m.deficit_total_percent}%` : '—'}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex justify-end gap-1">
                            <button
                              onClick={() => showSaved(m.run_id)}
                              title="Показать на странице"
                              className="flex items-center gap-1 rounded-md border border-stone-300 px-2 py-1 text-xs font-medium text-stone-700 hover:bg-stone-50"
                            >
                              <Eye className="h-3.5 w-3.5" /> Показать
                            </button>
                            <button
                              onClick={() => downloadSaved(m)}
                              title="Скачать Excel"
                              className="flex items-center gap-1 rounded-md border border-stone-300 px-2 py-1 text-xs font-medium text-stone-700 hover:bg-stone-50"
                            >
                              <Download className="h-3.5 w-3.5" /> Excel
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="p-4 text-sm text-stone-400">
                  {query
                    ? 'Ничего не найдено'
                    : scope === 'recent'
                      ? 'За последний час результатов нет.'
                      : 'Сохранённых результатов нет.'}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      {result?.success && info && (
        <>
          {/* Key metrics */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard
              title="Целевая функция"
              value={info.objective_value ?? 0}
              subtitle="Меньше = лучше"
            />
            <MetricCard
              title="Ошибки севооборота"
              value={info.rotation_errors ?? 0}
              subtitle={`Штраф: ${info.rotation_cost ?? 0}`}
            />
            <MetricCard
              title="Недобор площадей"
              value={`${info.deficit_total_percent ?? 0}%`}
              subtitle={`Штраф: ${info.deficit_cost ?? 0}`}
            />
            <MetricCard
              title="Время решения"
              value={`${info.solve_time ?? 0} сек`}
              subtitle={info.status}
            />
          </div>

          {/* Агрономический балл плана по факторам формирования СПП.
              Блок появляется только у прогонов, где балл считался. */}
          {info.factor_score_area_avg != null && (
            <div className="rounded-xl border border-stone-200 bg-white p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h3 className="font-semibold text-stone-800">
                    Агрономический балл плана
                  </h3>
                  <p className="mt-1 text-sm text-stone-500">
                    Взвешенная по гектарам оценка пар «поле → культура» по факторам
                    формирования СПП. 100 — все посчитанные факторы в оптимуме.
                  </p>
                </div>
                <Link
                  to="/factors"
                  className="text-sm font-medium text-primary-700 hover:text-primary-800"
                >
                  Реестр факторов →
                </Link>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
                <MetricCard
                  title="Балл по площади"
                  value={`${info.factor_score_area_avg} / 100`}
                  subtitle={`Простое среднее: ${info.factor_score_avg ?? '—'}`}
                />
                <MetricCard
                  title="Полей с баллом"
                  value={info.factor_scored_fields ?? 0}
                  subtitle="Без многолетников и исключённых"
                />
                <MetricCard
                  title="Влияние на план"
                  value={
                    info.factor_weight
                      ? `${info.factor_weight_effective ?? info.factor_weight}`
                      : 'выключено'
                  }
                  subtitle={
                    info.factor_authority != null
                      ? `вес ${info.factor_weight} × ${Math.round(info.factor_authority * 100)}% решающих факторов`
                      : `Штраф: ${info.factor_cost ?? 0}`
                  }
                />
                {coverage && (
                  <MetricCard
                    title="Покрытие факторами"
                    value={`${Math.round(coverage.coverage * 100)}%`}
                    subtitle={`${coverage.available_weight} из ${coverage.scoring_weight} баллов веса`}
                  />
                )}
              </div>

              {info.two_stage && (
                <div className="mt-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
                  Балл улучшался <b>вторым этапом</b> — только среди решений, не
                  худших по севообороту и площадям. Программа площадей от
                  включения балла не пострадала ни на пункт.
                </div>
              )}

              {coverage && coverage.coverage < 0.5 && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  Балл посчитан по{' '}
                  <b>{coverage.available_weight} из {coverage.scoring_weight}</b>{' '}
                  баллов веса: данных по остальным факторам в системе нет. Что именно
                  нужно догрузить — на странице{' '}
                  <Link to="/factors" className="underline">Факторы СПП</Link>.
                </div>
              )}

              {info.hard_block_pred_enabled && (
                <div className="mt-3 text-xs text-stone-500">
                  Жёсткий запрет недопустимого предшественника включён:
                  исключено пар «поле → культура» — <b>{info.hard_blocked_pred_pairs ?? 0}</b>.
                </div>
              )}
            </div>
          )}

          {/* Objective decomposition chart */}
          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <h3 className="mb-4 font-semibold text-stone-800">Декомпозиция целевой функции</h3>
            <div className="grid gap-6 lg:grid-cols-2">
              <ResponsiveContainer width="100%" height={250}>
                <BarChart
                  data={[
                    {
                      name: 'Ошибки севооборота',
                      value: info.rotation_cost ?? 0,
                      detail: `${info.rotation_errors} x ${WEIGHT_ROTATION_ERROR}`,
                    },
                    {
                      name: 'Недобор площадей',
                      value: info.deficit_cost ?? 0,
                      detail: `${info.deficit_total_percent}% x ${WEIGHT_AREA_DEFICIT}`,
                    },
                  ]}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                    <Cell fill="#ef4444" />
                    <Cell fill="#22c55e" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>

              {/* Chemical violations */}
              <div>
                <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-stone-700">
                  <AlertTriangle className="h-4 w-4" />
                  Нарушения химических ограничений
                </h4>
                <div className="grid grid-cols-2 gap-4">
                  <div className="rounded-lg bg-red-50 p-4 text-center">
                    <p className="text-2xl font-bold text-red-700">
                      {result.original_violations?.length ?? 0}
                    </p>
                    <p className="text-xs text-red-600">До оптимизации</p>
                  </div>
                  <div className="rounded-lg bg-green-50 p-4 text-center">
                    <p className="text-2xl font-bold text-green-700">0</p>
                    <p className="text-xs text-green-600">После оптимизации</p>
                  </div>
                </div>
                {result.original_violations && result.original_violations.length > 0 && (
                  <div className="mt-3 text-xs text-stone-600">
                    <p className="font-medium">
                      Устранённые нарушения ({result.original_violations.length}):
                    </p>
                    <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-stone-100 bg-stone-50 p-2">
                      {result.original_violations.map((v, i) => (
                        <p key={i}>
                          Поле {v.field_id}: {v.culture} ({v.area_ha} га)
                        </p>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Deficit analysis */}
          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <h3 className="mb-4 font-semibold text-stone-800">Анализ недоборов площадей</h3>
            {info.deficit_by_culture && Object.keys(info.deficit_by_culture).length > 0 ? (
              <div className="grid gap-6 lg:grid-cols-2">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-stone-200">
                      <th className="px-3 py-2 text-left font-semibold text-stone-700">Культура</th>
                      <th className="px-3 py-2 text-right font-semibold text-stone-700">План (га)</th>
                      <th className="px-3 py-2 text-right font-semibold text-stone-700">Недобор (га)</th>
                      <th className="px-3 py-2 text-right font-semibold text-stone-700">Недобор (%)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {Object.entries(info.deficit_by_culture)
                      .sort(([, a], [, b]) => b.deficit_ha - a.deficit_ha)
                      .map(([culture, data]) => (
                        <tr key={culture}>
                          <td className="px-3 py-2 text-stone-800">{culture}</td>
                          <td className="px-3 py-2 text-right">{data.target_ha.toFixed(0)}</td>
                          <td className="px-3 py-2 text-right text-red-600">{data.deficit_ha.toFixed(0)}</td>
                          <td className="px-3 py-2 text-right text-red-600">{data.deficit_percent}%</td>
                        </tr>
                      ))}
                  </tbody>
                </table>

                <ResponsiveContainer width="100%" height={250}>
                  <BarChart
                    data={Object.entries(info.deficit_by_culture).map(([culture, data]) => ({
                      name: culture.length > 15 ? culture.slice(0, 15) + '...' : culture,
                      value: data.deficit_ha,
                    }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-30} textAnchor="end" height={60} />
                    <YAxis />
                    <Tooltip />
                    <Bar dataKey="value" fill="#ef4444" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-green-700">
                <CheckCircle className="h-5 w-5" />
                <span>Все культуры достигли 100% целевой площади!</span>
              </div>
            )}
          </div>

          {/* Plan vs Fact comparison */}
          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <h3 className="mb-4 font-semibold text-stone-800">План vs Факт по культурам</h3>

            {result.comparison && (
              <>
                <ResponsiveContainer width="100%" height={350}>
                  <BarChart data={result.comparison}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      dataKey="culture"
                      tick={{ fontSize: 10 }}
                      angle={-35}
                      textAnchor="end"
                      height={80}
                    />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="target" name="План" fill="#93c5fd" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="actual" name="Факт" fill="#1d4ed8" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>

                <div className="mt-4 max-h-[300px] overflow-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-stone-50">
                      <tr className="border-b border-stone-200">
                        <th className="px-3 py-2 text-left font-semibold text-stone-700">Культура</th>
                        <th className="px-3 py-2 text-right font-semibold text-stone-700">План (га)</th>
                        <th className="px-3 py-2 text-right font-semibold text-stone-700">Факт (га)</th>
                        <th className="px-3 py-2 text-right font-semibold text-stone-700">Выполнение (%)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-stone-100">
                      {result.comparison.map((row) => (
                        <tr key={row.culture}>
                          <td className="px-3 py-2 text-stone-800">{row.culture}</td>
                          <td className="px-3 py-2 text-right">{row.target.toFixed(0)}</td>
                          <td className="px-3 py-2 text-right">{row.actual.toFixed(0)}</td>
                          <td
                            className={`px-3 py-2 text-right font-medium ${
                              row.percent >= 100 ? 'text-green-600' : row.percent >= 90 ? 'text-yellow-600' : 'text-red-600'
                            }`}
                          >
                            {row.percent.toFixed(1)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          {/* Карта плана (картинка) */}
          <div className="rounded-xl border border-stone-200 bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 font-semibold text-stone-800">
                <ImageIcon className="h-4 w-4" />
                Карта плана
              </h3>
              <div className="flex items-center gap-2">
                {planFarms.length > 1 && (
                  <select
                    value={imgFarm}
                    onChange={(e) => setImgFarm(e.target.value)}
                    className="rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                  >
                    <option value="">Все хозяйства</option>
                    {planFarms.map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </select>
                )}
                <button
                  onClick={handleDownloadImage}
                  disabled={!imgBlob}
                  className="flex items-center gap-2 rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Download className="h-4 w-4" />
                  Скачать PNG
                </button>
              </div>
            </div>
            {imgLoading && <p className="py-8 text-center text-sm text-stone-400">Построение карты…</p>}
            {imgError && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                Не удалось построить карту: {imgError}
              </div>
            )}
            {!imgLoading && !imgError && imgUrl && (
              <img
                src={imgUrl}
                alt="Карта плана по хозяйствам"
                className="mx-auto max-h-[70vh] w-full rounded-lg border border-stone-100 object-contain"
              />
            )}
          </div>

          {/* Export buttons */}
          <div className="flex gap-4">
            <button
              onClick={handleExportCSV}
              className="flex items-center gap-2 rounded-lg border border-stone-300 bg-white px-5 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-50"
            >
              <Download className="h-4 w-4" />
              Скачать CSV
            </button>
            <button
              onClick={handleExportExcel}
              className="flex items-center gap-2 rounded-lg border border-stone-300 bg-white px-5 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-50"
            >
              <Download className="h-4 w-4" />
              Скачать Excel
            </button>
          </div>
        </>
      )}

      {result && !result.success && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-5">
          <p className="font-medium text-red-700">Оптимизация не удалась</p>
          <p className="mt-1 text-sm text-red-600">
            Статус: {info?.status
              || Object.values(result.per_farm_info ?? {}).find((entry) => entry.info?.status)?.info?.status
              || 'не получен'}
          </p>
          {result.message && (
            <p className="mt-2 text-sm text-red-700">{result.message}</p>
          )}
          {result.per_farm_info && (
            <ul className="mt-3 space-y-1 text-sm text-red-700">
              {Object.entries(result.per_farm_info)
                .filter(([, entry]) => !entry.success)
                .map(([farm, entry]) => (
                  <li key={farm}>
                    {farm}: {entry.info?.status || 'без решения'}
                    {entry.info?.infeasible_reason
                      ? `. ${entry.info.infeasible_reason}`
                      : ''}
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      {!result && (
        <div className="rounded-xl border border-stone-200 bg-white p-12 text-center">
          <p className="text-stone-400">
            Постройте план на странице «Планирование одного хозяйства» или откройте
            сохранённый результат из списка выше.
          </p>
        </div>
      )}
    </div>
  )
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Привести meta сохранённого результата к RunMeta (для подписей/имён файлов). */
function metaToRunMeta(m: SavedResultMeta): RunMeta {
  return {
    runId: m.run_id,
    runAt: Date.parse(m.created_at) || Date.now(),
    planningYear: m.planning_year,
    farms: m.farms ?? [],
  }
}

/** Подпись хозяйства для шапки результата. */
function farmLabel(meta: RunMeta): string {
  if (meta.farms.length === 1) return meta.farms[0]
  if (meta.farms.length > 1) return `Все хозяйства (${meta.farms.length})`
  return 'Все хозяйства'
}

function fmtDateTime(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Имя файла экспорта: <хозяйство>_<время>_<uid>.<ext>. */
function buildExportName(meta: RunMeta | null, ext: 'csv' | 'xlsx' | 'png'): string {
  if (!meta) return `plan_export.${ext}`
  const farm =
    meta.farms.length === 1 ? meta.farms[0] : meta.farms.length > 1 ? 'все_хозяйства' : 'план'
  const safeFarm = farm.replace(/[\\/:*?"<>|\s]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')
  const d = new Date(meta.runAt)
  const p = (n: number) => String(n).padStart(2, '0')
  const time = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`
  return `${safeFarm}_${time}_${meta.runId}.${ext}`
}

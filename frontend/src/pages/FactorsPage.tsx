import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  useFactors, useFactorsCoverage,
  type FactorRecord, type FactorCoverageEntry,
} from '@/hooks/useData'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import MetricCard from '@/components/shared/MetricCard'
import { PLANNING_YEAR } from '@/config'

type Status = FactorCoverageEntry['status']

const STATUS_LABEL: Record<Status, string> = {
  available: 'Считается',
  no_data: 'Нет данных',
  implemented: 'Работает как фильтр',
  not_implemented: 'Не реализовано',
}

const STATUS_CLASS: Record<Status, string> = {
  available: 'bg-green-50 text-green-700 border-green-200',
  no_data: 'bg-amber-50 text-amber-800 border-amber-200',
  implemented: 'bg-primary-50 text-primary-800 border-primary-200',
  not_implemented: 'bg-stone-100 text-stone-600 border-stone-200',
}

const ROLE_LABEL: Record<string, string> = {
  hard_blocker: 'Жёсткий фильтр',
  bonus: 'Бонус к баллу',
  modifier: 'Модификатор',
  objective: 'Целевая функция',
  constraint: 'Ресурсное ограничение',
}

function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={
        'inline-block whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium ' +
        STATUS_CLASS[status]
      }
    >
      {STATUS_LABEL[status]}
    </span>
  )
}

function BandsTable({ factor }: { factor: FactorRecord }) {
  if (factor.bands.length === 0 && factor.enum_values.length === 0) return null
  return (
    <div className="mt-3 space-y-3">
      {factor.bands.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead className="bg-stone-50 text-left font-semibold text-stone-700">
              <tr>
                {factor.norm_kind === 'numeric_texture' && <th className="px-3 py-1.5">Гранулометрия</th>}
                <th className="px-3 py-1.5">Вилка</th>
                <th className="px-3 py-1.5">Агрономическая зона</th>
                <th className="px-3 py-1.5 text-right">K</th>
                <th className="px-3 py-1.5 text-right">Балл</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100">
              {factor.bands.map((band, i) => (
                <tr key={`${band.texture}-${band.order}-${i}`}>
                  {factor.norm_kind === 'numeric_texture' && (
                    <td className="px-3 py-1.5 text-stone-500">{band.texture}</td>
                  )}
                  <td className="px-3 py-1.5 font-mono text-stone-700">{band.range}</td>
                  <td className="px-3 py-1.5 text-stone-600">{band.zone}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{band.k}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium">{band.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {factor.enum_values.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead className="bg-stone-50 text-left font-semibold text-stone-700">
              <tr>
                <th className="px-3 py-1.5">Значение</th>
                <th className="px-3 py-1.5 text-right">K</th>
                <th className="px-3 py-1.5">Комментарий</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100">
              {factor.enum_values.map((v) => (
                <tr key={v.value}>
                  <td className="px-3 py-1.5 text-stone-700">{v.value}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{v.k}</td>
                  <td className="px-3 py-1.5 text-stone-500">{v.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function FactorsPage() {
  const { data: registry, isLoading, isError, error } = useFactors()
  const { data: coverage } = useFactorsCoverage(PLANNING_YEAR)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const statusById = useMemo(() => {
    const map = new Map<string, FactorCoverageEntry>()
    for (const entry of coverage?.factors ?? []) map.set(entry.id, entry)
    return map
  }, [coverage])

  const byCategory = useMemo(() => {
    const groups = new Map<string, FactorRecord[]>()
    for (const factor of registry?.factors ?? []) {
      const bucket = groups.get(factor.category)
      if (bucket) bucket.push(factor)
      else groups.set(factor.category, [factor])
    }
    return Array.from(groups, ([category, factors]) => ({ category, factors }))
  }, [registry])

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  if (isLoading) return <LoadingSpinner text="Загрузка реестра факторов…" />
  if (isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        Не удалось загрузить реестр факторов: {String(error)}
      </div>
    )
  }
  if (!registry) return null

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-stone-900">Факторы формирования СПП</h2>
        <p className="mt-1 max-w-3xl text-sm text-stone-500">
          Реестр из «Факторы_формирования_СПП» заказчика: {registry.count} факторов
          общим весом {registry.total_weight}. Каждый фактор приводит своё значение
          к коэффициенту K от 0 до 1, где 1 — агрономический оптимум; вклад в
          балл равен K × вес. Факторы, для которых нет исходных данных, не
          занижают балл, а выбывают из знаменателя — вместе с ними падает
          покрытие.
        </p>
      </div>

      {coverage && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard
              title="Покрытие факторами"
              value={`${Math.round(coverage.coverage * 100)}%`}
              subtitle={`${coverage.available_weight} из ${coverage.scoring_weight} баллов веса`}
            />
            <MetricCard
              title="Баллов в модели"
              value={coverage.scoring_weight}
              subtitle="Остальное — ресурсные ограничения"
            />
            <MetricCard
              title="Ресурсные ограничения"
              value={coverage.constraint_weight}
              subtitle="Флот, капитал, сушка"
            />
            <MetricCard
              title="Подключено источников"
              value={coverage.sources_present.length}
              subtitle={coverage.sources_present.join(', ') || 'только данные севооборота'}
            />
          </div>

          {coverage.available_weight < coverage.scoring_weight && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-medium">
                Чем догрузить недостающие {coverage.scoring_weight - coverage.available_weight} баллов
              </p>
              <p className="mt-1">
                Достаточно положить CSV в <code>backend/data/</code> — факторы
                подключатся сами. Шаблоны с описанием колонок лежат в{' '}
                <code>backend/data/templates/</code>, подробности — в{' '}
                <code>docs/FACTORS_SPP.md</code>.
              </p>
              <ul className="mt-2 space-y-0.5">
                {Array.from(
                  new Set(
                    coverage.factors
                      .filter((f) => f.status === 'no_data')
                      .map((f) => f.data_source),
                  ),
                ).map((source) => (
                  <li key={source}>
                    · <code>{source}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {byCategory.map(({ category, factors }) => (
        <div key={category} className="rounded-xl border border-stone-200 bg-white">
          <div className="flex items-baseline justify-between border-b border-stone-100 px-5 py-3">
            <h3 className="font-semibold text-stone-800">{category}</h3>
            <span className="text-sm text-stone-500">
              вес {factors.reduce((s, f) => s + f.weight, 0)}
            </span>
          </div>
          <div className="divide-y divide-stone-100">
            {factors.map((factor) => {
              const entry = statusById.get(factor.id)
              const isOpen = expanded.has(factor.id)
              const hasDetails = factor.bands.length > 0 || factor.enum_values.length > 0
              return (
                <div key={factor.id} className="px-5 py-3">
                  <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
                    <button
                      onClick={() => hasDetails && toggle(factor.id)}
                      disabled={!hasDetails}
                      className="mt-0.5 text-stone-400 hover:text-stone-600 disabled:opacity-0"
                      title="Показать вилки нормализации"
                    >
                      {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </button>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-stone-900">{factor.name}</span>
                        <span className="text-xs text-stone-400">
                          {ROLE_LABEL[factor.role] ?? factor.role}
                        </span>
                        {entry && <StatusBadge status={entry.status} />}
                      </div>
                      <p className="mt-1 text-sm text-stone-600">{factor.description}</p>
                      <p className="mt-1 text-xs text-stone-400">
                        Источник: <code>{factor.data_source}</code>
                        {factor.unit && <> · единицы: {factor.unit}</>}
                        {entry?.fields_covered != null && (
                          <> · полей с данными: {Math.round(entry.fields_covered * 100)}%</>
                        )}
                      </p>
                      {factor.notes && (
                        <p className="mt-2 rounded-md bg-stone-50 p-2 text-xs text-stone-600">
                          {factor.notes}
                        </p>
                      )}
                      {isOpen && <BandsTable factor={factor} />}
                    </div>

                    <div className="w-16 shrink-0 text-right">
                      <div className="text-lg font-semibold tabular-nums text-stone-900">
                        {factor.weight}
                      </div>
                      <div className="text-xs text-stone-400">баллов</div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

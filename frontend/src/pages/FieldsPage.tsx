import { useState, useMemo } from 'react'
import { useFields, useFarms, useYears, useSubdivisions, useCultures, useSummary, useChemicalHistory } from '@/hooks/useData'
import MetricCard from '@/components/shared/MetricCard'
import LoadingSpinner from '@/components/shared/LoadingSpinner'
import { FlaskConical } from 'lucide-react'

export default function FieldsPage() {
  const [selectedYear, setSelectedYear] = useState<number>(2025)
  const [selectedFarm, setSelectedFarm] = useState<string>('')
  const [selectedSubdivision, setSelectedSubdivision] = useState<string>('')
  const [selectedCulture, setSelectedCulture] = useState<string>('')
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null)

  const { data: years = [] } = useYears()
  const { data: farms = [] } = useFarms(selectedYear)
  const { data: subdivisions = [] } = useSubdivisions(selectedFarm || undefined)
  const { data: cultures = [] } = useCultures(selectedYear, selectedFarm || undefined)
  const { data: summary } = useSummary(selectedYear, selectedFarm || undefined)
  const { data: fields = [], isLoading } = useFields(
    selectedYear,
    selectedFarm || undefined,
    selectedSubdivision || undefined,
    selectedCulture || undefined,
  )
  const { data: chemHistory = [] } = useChemicalHistory(selectedFieldId)

  const totalFilteredArea = useMemo(
    () => fields.reduce((s, f) => s + (f.area_ha || 0), 0),
    [fields],
  )

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-stone-900">Доступные поля</h2>

      {/* Summary metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard title="Всего полей" value={summary?.total_fields ?? '...'} />
        <MetricCard title="Общая площадь" value={summary ? `${summary.total_area.toLocaleString('ru-RU')} га` : '...'} />
        <MetricCard title="Для оптимизации" value={summary?.optimizable_fields ?? '...'} />
        <MetricCard
          title="Площадь оптимиз."
          value={summary ? `${summary.optimizable_area.toLocaleString('ru-RU')} га` : '...'}
        />
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className="block text-sm font-medium text-stone-700">Год</label>
          <select
            value={selectedYear}
            onChange={(e) => {
              setSelectedYear(Number(e.target.value))
              setSelectedFarm('')
              setSelectedSubdivision('')
              setSelectedCulture('')
            }}
            className="mt-1 block w-full rounded-lg border border-stone-300 px-3 py-2.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            {years.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-stone-700">Хозяйство</label>
          <select
            value={selectedFarm}
            onChange={(e) => {
              setSelectedFarm(e.target.value)
              setSelectedSubdivision('')
              setSelectedCulture('')
            }}
            className="mt-1 block w-full rounded-lg border border-stone-300 px-3 py-2.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Все</option>
            {farms.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-stone-700">Подразделение</label>
          <select
            value={selectedSubdivision}
            onChange={(e) => setSelectedSubdivision(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-stone-300 px-3 py-2.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Все</option>
            {subdivisions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-stone-700">Культура</label>
          <select
            value={selectedCulture}
            onChange={(e) => setSelectedCulture(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-stone-300 px-3 py-2.5 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Все</option>
            {cultures.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
      </div>

      <p className="text-sm text-stone-500">
        Найдено полей: <span className="font-semibold text-stone-800">{fields.length}</span>
        {' '}(площадь: {totalFilteredArea.toLocaleString('ru-RU')} га)
      </p>

      {/* Fields table */}
      {isLoading ? (
        <LoadingSpinner />
      ) : (
        <div className="rounded-xl border border-stone-200 bg-white">
          <div className="max-h-[500px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-stone-50">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold text-stone-700">Номер поля</th>
                  <th className="px-4 py-3 text-left font-semibold text-stone-700">Хозяйство</th>
                  <th className="px-4 py-3 text-left font-semibold text-stone-700">Подразделение</th>
                  <th className="px-4 py-3 text-left font-semibold text-stone-700">Культура</th>
                  <th className="px-4 py-3 text-right font-semibold text-stone-700">Площадь (га)</th>
                  <th className="px-4 py-3 text-right font-semibold text-stone-700">Балл плод.</th>
                  <th className="px-4 py-3 text-center font-semibold text-stone-700">Хим.</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {fields.map((field) => (
                  <tr
                    key={field.field_id}
                    className={`hover:bg-stone-50 cursor-pointer ${
                      selectedFieldId === field.field_id ? 'bg-primary-50' : ''
                    }`}
                    onClick={() => setSelectedFieldId(field.field_id)}
                  >
                    <td className="px-4 py-2.5 font-medium text-stone-800">{field.field_id}</td>
                    <td className="px-4 py-2.5 text-stone-600">{field.farm}</td>
                    <td className="px-4 py-2.5 text-stone-600">{field.subdivision}</td>
                    <td className="px-4 py-2.5 text-stone-800">
                      <span className={field.is_perennial ? 'text-stone-400 italic' : ''}>
                        {field.culture}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right text-stone-800">
                      {field.area_ha?.toFixed(0)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-stone-600">
                      {field.fertility_score?.toFixed(1) ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedFieldId(field.field_id)
                        }}
                        className="rounded p-1 text-stone-400 hover:text-primary-600"
                      >
                        <FlaskConical className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Chemical history panel */}
      {selectedFieldId && (
        <div className="rounded-xl border border-stone-200 bg-white p-5">
          <h3 className="mb-3 flex items-center gap-2 font-semibold text-stone-800">
            <FlaskConical className="h-5 w-5 text-primary-600" />
            История химобработки — поле {selectedFieldId}
          </h3>
          {chemHistory.length === 0 ? (
            <p className="text-sm text-stone-400">Нет данных о химобработке для этого поля.</p>
          ) : (
            <div className="space-y-2">
              {chemHistory.map((record) => (
                <div key={record.year} className="flex gap-3 text-sm">
                  <span className="font-semibold text-stone-700">{record.year}:</span>
                  <span className="text-stone-600">{record.substances.join(', ')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

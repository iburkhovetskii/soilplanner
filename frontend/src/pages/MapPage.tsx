import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { MapContainer, TileLayer, GeoJSON, AttributionControl, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { PathOptions, StyleFunction } from 'leaflet'
import { Upload } from 'lucide-react'
import {
  useCropColors, usePlanGeoJson, useYears,
  type CropColor, type PlanFeatureCollection,
} from '@/hooks/useData'
import { api } from '@/api/client'
import LoadingSpinner from '@/components/shared/LoadingSpinner'

// Запасная палитра — только для культур, которых нет в справочнике заказчика.
// Намеренно серая: пробел в справочнике должен быть виден глазом, а не
// маскироваться под ещё один «сельскохозяйственный» цвет.
const FALLBACK_PALETTE = [
  '#94a3b8', '#64748b', '#a1a1aa', '#78716c',
  '#6b7280', '#9ca3af', '#71717a', '#57534e',
]
function fallbackColor(culture: string | null): string {
  if (!culture) return FALLBACK_PALETTE[0]
  let h = 0
  for (let i = 0; i < culture.length; i++) h = (h * 31 + culture.charCodeAt(i)) >>> 0
  return FALLBACK_PALETTE[h % FALLBACK_PALETTE.length]
}

type Source = 'plan' | 'history' | 'file'

interface PlanRow {
  field_id: string
  culture_unified?: string
  culture?: string
  area_ha?: number
  farm?: string
  subdivision?: string
}

function parsePlanFile(text: string, filename: string): PlanRow[] {
  // JSON: либо массив строк, либо объект {optimized_plan: [...]} (формат бэка)
  const trimmed = text.trim()
  if (trimmed.startsWith('{') || trimmed.startsWith('[') || filename.endsWith('.json')) {
    const parsed = JSON.parse(trimmed)
    if (Array.isArray(parsed)) return parsed
    if (Array.isArray(parsed.optimized_plan)) return parsed.optimized_plan
    if (Array.isArray(parsed.plan)) return parsed.plan
    throw new Error('JSON не содержит ни массив строк, ни ключ optimized_plan/plan')
  }
  // CSV: первая строка — заголовки
  const lines = trimmed.split(/\r?\n/).filter(Boolean)
  if (lines.length < 2) throw new Error('CSV пустой')
  const sep = lines[0].includes(';') ? ';' : ','
  const headers = lines[0].split(sep).map((h) => h.trim().replace(/^"|"$/g, ''))
  return lines.slice(1).map((line) => {
    const cells = line.split(sep).map((c) => c.trim().replace(/^"|"$/g, ''))
    const obj: Record<string, string> = {}
    headers.forEach((h, i) => { obj[h] = cells[i] ?? '' })
    return {
      field_id: obj.field_id || obj['Полный номер поля'] || obj['field'] || '',
      culture_unified: obj.culture_unified || obj.culture || obj['Культура'],
      area_ha: obj.area_ha ? Number(obj.area_ha) : undefined,
      farm: obj.farm || obj['Хозяйство'],
      subdivision: obj.subdivision || obj['Подразделение'],
    }
  }).filter((r) => r.field_id)
}

/**
 * При загрузке файла зумит карту к первому полю плана, чтобы не искать его
 * вручную каждый раз. Срабатывает только для source='file'; для history/plan
 * сохраняется общий обзор по центроиду.
 */
function FitToFirstField({ data, source }: { data: PlanFeatureCollection | null; source: Source }) {
  const map = useMap()
  useEffect(() => {
    if (source !== 'file' || !data?.features?.length) return
    const g = data.features[0].geometry
    const rings = g.type === 'Polygon' ? [g.coordinates[0]] : g.coordinates.map((p: any) => p[0])
    const latlngs: [number, number][] = []
    for (const ring of rings) {
      for (const [lon, lat] of ring) latlngs.push([lat, lon])
    }
    if (latlngs.length === 0) return
    map.fitBounds(latlngs, { maxZoom: 13, padding: [40, 40] })
  }, [data, source, map])
  return null
}

export default function MapPage() {
  const { data: years } = useYears()
  const { data: cropColors } = useCropColors()
  const [source, setSource] = useState<Source>('history')
  const [year, setYear] = useState<number | null>(null)
  const [fileData, setFileData] = useState<PlanFeatureCollection | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [fileName, setFileName] = useState<string>('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Дефолт года: max-1 из доступных. У нас max — обычно последний загруженный год
  // данных (например 2026), но у него в parquet часто нет геометрий. Прошлый год
  // (max-1, например 2025) — тот «исторически богатый» срез, по которому в БД
  // есть и культуры, и контуры полей. Если данных только один год — используем его.
  useEffect(() => {
    if (year === null && years && years.length > 0) {
      const sorted = [...years].sort((a, b) => b - a)
      setYear(sorted[1] ?? sorted[0])
    }
  }, [year, years])

  // Эффективный год для запроса (с фолбэком, пока years ещё не загрузились)
  const effectiveYear = year ?? 2025
  // Для source='file' не дёргаем backend — данные уже в fileData
  const enabledSource = source === 'file' ? 'history' : source
  const { data: fetchedData, isLoading, isError, error } = usePlanGeoJson(
    enabledSource, effectiveYear,
  )
  const data = source === 'file' ? fileData : fetchedData

  const handleFile = async (file: File) => {
    setFileError(null)
    setFileLoading(true)
    setFileName(file.name)
    try {
      const text = await file.text()
      const rows = parsePlanFile(text, file.name)
      if (rows.length === 0) {
        throw new Error('В файле не найдено ни одного поля с field_id')
      }
      const gj = await api.post<PlanFeatureCollection>(
        '/optimization/plan-geojson-from-data',
        { plan: rows },
      )
      setFileData(gj)
      setSource('file')
    } catch (e) {
      setFileError(e instanceof Error ? e.message : String(e))
    } finally {
      setFileLoading(false)
    }
  }

  // Цвет культуры: справочник заказчика, иначе — серая заглушка
  const colorFor = (culture: string | null): string => {
    if (!culture) return FALLBACK_PALETTE[0]
    const entry = cropColors?.colors?.[culture]
    return entry?.color ?? fallbackColor(culture)
  }
  const infoFor = (culture: string): CropColor | undefined =>
    cropColors?.colors?.[culture]

  // Цвет каждого feature
  const style: StyleFunction = (feature) => {
    const c = feature?.properties?.culture ?? null
    return {
      fillColor: colorFor(c),
      color: '#ffffff',
      weight: 0.5,
      fillOpacity: 0.85,
    } as PathOptions
  }

  // Легенда: культуры плана, сгруппированные по категории справочника.
  // Не опознанные справочником собираются в отдельный блок — это готовый
  // список того, чем нужно пополнить crop_colors.csv.
  const legendGroups = useMemo(() => {
    if (!data?.features) return [] as Array<{ category: string; cultures: string[] }>
    const set = new Set<string>()
    for (const f of data.features) {
      if (f.properties?.culture) set.add(f.properties.culture)
    }
    const byCategory = new Map<string, string[]>()
    for (const culture of Array.from(set).sort()) {
      const entry = cropColors?.colors?.[culture]
      const category = entry?.matched
        ? (entry.category || 'Без категории')
        : 'Нет в справочнике'
      const bucket = byCategory.get(category)
      if (bucket) bucket.push(culture)
      else byCategory.set(category, [culture])
    }
    return Array.from(byCategory, ([category, cultures]) => ({ category, cultures }))
      .sort((a, b) =>
        a.category === 'Нет в справочнике' ? 1
          : b.category === 'Нет в справочнике' ? -1
            : a.category.localeCompare(b.category))
  }, [data, cropColors])

  const legendCount = legendGroups.reduce((n, g) => n + g.cultures.length, 0)
  const unmatchedCount =
    legendGroups.find((g) => g.category === 'Нет в справочнике')?.cultures.length ?? 0

  // Центр карты — bbox features
  const center = useMemo(() => {
    if (!data?.features?.length) return [55, 50] as [number, number]
    let lonSum = 0, latSum = 0, n = 0
    for (const f of data.features) {
      const g = f.geometry
      const rings = g.type === 'Polygon' ? [g.coordinates[0]] : g.coordinates.map((p: any) => p[0])
      for (const ring of rings) {
        for (const [lon, lat] of ring) {
          lonSum += lon; latSum += lat; n++
        }
      }
    }
    return [latSum / n, lonSum / n] as [number, number]
  }, [data])

  const onEachFeature = (feature: any, layer: any) => {
    const p = feature.properties
    const info = p.culture ? infoFor(p.culture) : undefined
    layer.bindTooltip(
      `<b>${p.culture ?? '—'}</b><br/>` +
      (info?.short_name && info.short_name !== p.culture
        ? `<span style="color:#64748b">${info.short_name}</span><br/>`
        : '') +
      `${p.field_id}<br/>` +
      `${p.area_ha?.toFixed?.(1) ?? '?'} га` +
      (p.farm ? `<br/><span style="color:#64748b">${p.farm}</span>` : ''),
      { sticky: true, opacity: 0.95 },
    )
  }

  // Опции года для dropdown: все доступные годы данных
  const yearOptions = useMemo(() => {
    if (!years || years.length === 0) return [effectiveYear]
    return [...years].sort((a, b) => b - a)
  }, [years, effectiveYear])

  const isEmpty = !!data && data.features.length === 0
  const hasFeatures = !!data && data.features.length > 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-stone-900">Карта плана</h2>
          <p className="mt-1 text-sm text-stone-500">
            {source === 'plan'
              ? `План за ${effectiveYear} (последний прогон оптимизатора)`
              : source === 'history'
                ? `Факт за ${effectiveYear} год`
                : `План из файла: ${fileName || '—'}`}
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-medium text-stone-500">Источник</label>
            <div className="mt-1 flex gap-1 rounded-lg border border-stone-300 bg-white p-1">
              <button
                onClick={() => setSource('plan')}
                title="План оптимизатора (последний прогон)"
                className={
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors ' +
                  (source === 'plan'
                    ? 'bg-primary-600 text-white'
                    : 'text-stone-700 hover:bg-stone-100')
                }
              >
                План оптимизатора
              </button>
              <button
                onClick={() => setSource('history')}
                title="Исторический факт за выбранный год"
                className={
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors ' +
                  (source === 'history'
                    ? 'bg-primary-600 text-white'
                    : 'text-stone-700 hover:bg-stone-100')
                }
              >
                Факт за год
              </button>
              <button
                onClick={() => fileData && setSource('file')}
                disabled={!fileData}
                title={fileData ? 'Показать план, загруженный из файла' : 'Сначала загрузите файл справа'}
                className={
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ' +
                  (source === 'file'
                    ? 'bg-primary-600 text-white'
                    : 'text-stone-700 hover:bg-stone-100')
                }
              >
                План из файла
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-stone-500">
              Год {source !== 'history' ? '(для подписи)' : ''}
            </label>
            <select
              value={effectiveYear}
              onChange={(e) => setYear(Number(e.target.value))}
              className="mt-1 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              {yearOptions.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-stone-500">Файл плана</label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,.csv"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) handleFile(f)
                e.target.value = ''
              }}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={fileLoading}
              title="JSON или CSV с колонками field_id, culture_unified"
              className="mt-1 flex items-center gap-2 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-50"
            >
              <Upload className="h-4 w-4" />
              {fileLoading ? 'Загрузка…' : 'Загрузить'}
            </button>
          </div>
        </div>
      </div>

      {fileError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Ошибка чтения файла: {fileError}
        </div>
      )}
      {source === 'file' && fileData && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          Загружен файл <b>{fileName}</b>: {fileData.n_features ?? fileData.features.length} полей
          с геометрией{' '}
          <button
            className="ml-2 text-emerald-700 underline hover:text-emerald-900"
            onClick={() => { setFileData(null); setFileName(''); setSource('history') }}
          >
            убрать
          </button>
        </div>
      )}

      {isLoading && <LoadingSpinner />}
      {isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Ошибка загрузки: {String(error)}
        </div>
      )}

      {/* Empty state */}
      {isEmpty && source === 'plan' && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
          <p className="font-medium">Оптимизация ещё не запускалась.</p>
          <p className="mt-1 text-amber-800">
            Перейдите на «Ввод данных», задайте цели и нажмите «Запустить оптимизацию».
            {data?.message ? ` (${data.message})` : ''}
          </p>
          <Link
            to="/targets"
            className="mt-3 inline-block rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            Перейти к вводу данных
          </Link>
        </div>
      )}
      {isEmpty && source === 'history' && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">
            Нет геометрий для {effectiveYear} года.
          </p>
          <p className="mt-1 text-amber-800">
            Попробуйте выбрать другой год в выпадающем списке выше.
            {data?.message ? ` (${data.message})` : ''}
          </p>
        </div>
      )}
      {isEmpty && source === 'file' && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">В загруженном файле нет полей с известной геометрией.</p>
          <p className="mt-1 text-amber-800">
            Проверьте, что в колонке field_id используются те же ID, что в каталоге полей.
          </p>
        </div>
      )}

      {hasFeatures && (
        <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
          <div className="overflow-hidden rounded-xl border border-stone-200 bg-white"
               style={{ height: '70vh' }}>
            <MapContainer
              center={center}
              zoom={9}
              style={{ height: '100%', width: '100%' }}
              attributionControl={false}
            >
              <TileLayer
                attribution='&copy; OpenStreetMap'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <AttributionControl position="bottomright" prefix={false} />
              <FitToFirstField data={data} source={source} />
              <GeoJSON
                // Палитра приезжает отдельным запросом и обычно позже геометрии.
                // style в react-leaflet применяется только при монтировании
                // слоя, поэтому её готовность входит в key — иначе поля
                // остались бы серыми до перезагрузки страницы.
                key={`${source}-${effectiveYear}-${fileName}-${cropColors ? 'palette' : 'raw'}`}
                data={data as any}
                style={style as any}
                onEachFeature={onEachFeature}
              />
            </MapContainer>
          </div>

          <div className="rounded-xl border border-stone-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-stone-800">
              Культуры ({legendCount})
            </h3>
            <div className="max-h-[60vh] overflow-y-auto pr-2 text-xs space-y-3">
              {legendGroups.map((group) => (
                <div key={group.category}>
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-stone-400">
                    {group.category}
                  </div>
                  <div className="space-y-1.5">
                    {group.cultures.map((c) => (
                      <div key={c} className="flex items-center gap-2">
                        <span
                          className="inline-block h-3 w-4 flex-shrink-0 rounded border border-stone-200"
                          style={{ background: colorFor(c) }}
                        />
                        <span className="text-stone-700">{c}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 space-y-1 border-t border-stone-100 pt-3 text-xs text-stone-500">
              <div>Полей на карте: <b>{data.n_features ?? data.features.length}</b></div>
              {cropColors && (
                <div>
                  Цвета — справочник 1С ({cropColors.reference_size} культур)
                  {unmatchedCount > 0 && (
                    <span className="text-amber-700">
                      {' '}· {unmatchedCount} не опознано
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

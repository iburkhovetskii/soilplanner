import { useCallback } from 'react'
import { api } from '@/api/client'
import { useTargetsStore } from '@/stores/targets'
import { useResultsStore, type OptimizationResult } from '@/stores/results'

interface StreamMessage {
  type: string
  message?: string
  data?: OptimizationResult
  objective?: number
}

/**
 * Shared hook for running optimization.
 * Используется как на TargetsPage (кнопка "Запустить оптимизацию"), так и на ResultsPage.
 * Возвращает run() — async-функция запуска; статус и результат хранятся в useResultsStore.
 */
export function useOptimizationRun() {
  const {
    cropTargets, planningYear, hardRatio, softRatio,
    weightCompactness, weightAdjacency, weightFactorScore,
  } = useTargetsStore()
  const { isRunning, statusMessage, setResult, setRunMeta, setRunning, setStatusMessage } =
    useResultsStore()

  const run = useCallback(async (
    overrides?: {
      cropTargetsOverride?: Record<string, Record<string, number>>
      planningYearOverride?: number
      fieldOverridesOverride?: Record<string, string>
    },
  ): Promise<boolean> => {
    setRunning(true)
    setStatusMessage('Запуск оптимизации...')

    const effectiveTargets = overrides?.cropTargetsOverride ?? cropTargets
    const effectiveYear = overrides?.planningYearOverride ?? planningYear ?? 2026
    const effectiveFieldOverrides = overrides?.fieldOverridesOverride ?? {}

    // Контекст запуска: хозяйства из целей, год, уникальный id и время.
    const runId = makeRunId()
    setRunMeta({
      runId,
      runAt: Date.now(),
      planningYear: effectiveYear,
      farms: Object.keys(effectiveTargets),
    })

    let success = false
    try {
      await api.streamOptimization(
        {
          crop_targets:
            Object.keys(effectiveTargets).length > 0 ? effectiveTargets : null,
          hard_ratio: hardRatio,
          soft_ratio: softRatio,
          planning_year: effectiveYear,
          weight_compactness: weightCompactness,
          weight_adjacency: weightAdjacency,
          weight_factor_score: weightFactorScore,
          field_overrides:
            Object.keys(effectiveFieldOverrides).length > 0 ? effectiveFieldOverrides : null,
          run_id: runId,
        },
        (msg: unknown) => {
          const data = msg as StreamMessage
          if (data.type === 'status') {
            setStatusMessage(data.message || '')
          } else if (data.type === 'progress') {
            setStatusMessage(`Решение найдено... objective=${data.objective}`)
          } else if (data.type === 'result' && data.data) {
            setResult(data.data)
            success = true
          } else if (data.type === 'error') {
            setStatusMessage(`Ошибка: ${data.message}`)
            setRunning(false)
          }
        },
      )
    } catch (err) {
      setStatusMessage(`Ошибка: ${err instanceof Error ? err.message : 'unknown'}`)
      return false
    } finally {
      setRunning(false)
    }
    if (!success) {
      setStatusMessage('Расчёт закончился, но ответ не удалось прочитать')
    }
    return success
  }, [
    cropTargets,
    planningYear,
    hardRatio,
    softRatio,
    weightCompactness,
    weightAdjacency,
    weightFactorScore,
    setResult,
    setRunMeta,
    setRunning,
    setStatusMessage,
  ])

  return { run, isRunning, statusMessage }
}

/** Короткий уникальный id запуска (8 hex-символов). */
function makeRunId(): string {
  const c = globalThis.crypto
  if (c?.randomUUID) return c.randomUUID().slice(0, 8)
  return Math.random().toString(16).slice(2, 10)
}

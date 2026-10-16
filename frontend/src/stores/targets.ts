import { create } from 'zustand'

export type PerFarmTargets = Record<string, Record<string, number>>

interface TargetsState {
  // Per-farm цели: { farm: { culture: ha } }
  cropTargets: PerFarmTargets
  planningYear: number | null
  hardRatio: number
  softRatio: number
  weightCompactness: number
  weightAdjacency: number
  weightFactorScore: number

  setCropTargets: (targets: PerFarmTargets) => void
  setPlanningYear: (year: number) => void
  updateTarget: (farm: string, culture: string, area: number) => void
  removeTarget: (farm: string, culture: string) => void
  setHardRatio: (ratio: number) => void
  setSoftRatio: (ratio: number) => void
  setWeightCompactness: (w: number) => void
  setWeightAdjacency: (w: number) => void
  setWeightFactorScore: (w: number) => void
}

export const useTargetsStore = create<TargetsState>((set) => ({
  cropTargets: {},
  planningYear: null,
  hardRatio: 0.9,
  softRatio: 1.0,
  weightCompactness: 0,
  weightAdjacency: 2, // вес гео-кучности из эксперимента (approach1_low); чекбол on/off в UI
  weightFactorScore: 3, // балл факторов СПП; 3 — иерархия весов сохраняется (см. constants.py)

  setCropTargets: (targets) => set({ cropTargets: targets }),
  setPlanningYear: (year) => set({ planningYear: year, cropTargets: {} }),
  updateTarget: (farm, culture, area) =>
    set((state) => ({
      cropTargets: {
        ...state.cropTargets,
        [farm]: { ...(state.cropTargets[farm] ?? {}), [culture]: area },
      },
    })),
  removeTarget: (farm, culture) =>
    set((state) => {
      const farmTargets = state.cropTargets[farm] ?? {}
      const { [culture]: _, ...rest } = farmTargets
      return { cropTargets: { ...state.cropTargets, [farm]: rest } }
    }),
  setHardRatio: (ratio) => set({ hardRatio: ratio }),
  setSoftRatio: (ratio) => set({ softRatio: ratio }),
  setWeightCompactness: (w) => set({ weightCompactness: w }),
  setWeightAdjacency: (w) => set({ weightAdjacency: w }),
  setWeightFactorScore: (w) => set({ weightFactorScore: w }),
}))

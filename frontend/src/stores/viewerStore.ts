import { create } from 'zustand'

export type ViewerTab = 'overview' | 'cad' | 'ocr' | 'vision' | 'fusion'

interface ViewerState {
  activeTab: ViewerTab
  showCad: boolean
  showOcr: boolean
  showVision: boolean
  selectedId: string | null
  selectedKind: 'entity' | 'ocr' | 'vision' | 'feature' | null
  setActiveTab: (tab: ViewerTab) => void
  toggleLayer: (layer: 'cad' | 'ocr' | 'vision') => void
  select: (id: string | null, kind?: ViewerState['selectedKind']) => void
}

export const useViewerStore = create<ViewerState>((set) => ({
  activeTab: 'overview',
  showCad: true,
  showOcr: true,
  showVision: true,
  selectedId: null,
  selectedKind: null,
  setActiveTab: (activeTab) => set({ activeTab }),
  toggleLayer: (layer) => {
    const key = ('show' + layer[0].toUpperCase() + layer.slice(1)) as 'showCad' | 'showOcr' | 'showVision'
    set((state) => ({ [key]: !state[key] }))
  },
  select: (selectedId, selectedKind = null) => set({ selectedId, selectedKind }),
}))

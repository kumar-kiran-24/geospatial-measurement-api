'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ArrowLeft, LoaderCircle, MapPinned, AlertTriangle } from 'lucide-react'
import 'leaflet/dist/leaflet.css'
import { MapContainer, TileLayer, GeoJSON } from 'react-leaflet'
import L from 'leaflet'
import { apiClient } from '../../../lib/api/client'

type FileDetail = {
  id: string | number
  filename?: string
  original_filename?: string
  name?: string
  file_name?: string
  format?: string
  file_format?: string
  file_type?: string
  status?: string
  processing_status?: string
  uploaded_at?: string
  created_at?: string
  file_size?: number
  size?: number
  size_bytes?: number
  feature_count?: number
  polygon_area?: number
  linestring_length?: number
  error?: string
  error_detail?: string
  processing_error?: { code?: string; message?: string }
  source_crs?: string | null
  source_crs_name?: string | null
  source_crs_kind?: string | null
}

type MeasurementsResponse = {
  file?: FileDetail
  summary?: {
    total_area_square_meters?: number
    total_length_meters?: number
  }
  features?: Array<{
    feature_index: number
    feature_id?: string | null
    geometry_type: string
    measurement_status: string
    measurement_type: string
    area?: { square_meters: number; square_kilometers: number; hectares: number } | null
    length?: { meters: number; kilometers: number } | null
    error?: { code: string; message: string } | null
    properties?: Record<string, unknown> | null
    geometry?: any | null
  }>
}

const nameOf = (file: FileDetail) => file.filename || file.original_filename || file.name || file.file_name || 'Untitled file'
const formatOf = (file: FileDetail) => (file.format || file.file_format || file.file_type || nameOf(file).split('.').pop() || 'file').replace('.', '').toUpperCase()
const numberOf = (value?: number) => value === undefined || value === null ? 'Not available' : new Intl.NumberFormat().format(value)

// Fix default Leaflet icon paths for Next.js
delete (L.Icon.Default.prototype as any)._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

function styleByType(type: string) {
  const base = { weight: 2, opacity: 0.9 }
  if (type.includes('Polygon')) return { ...base, color: '#22c55e', fillColor: '#22c55e', fillOpacity: 0.25 }
  if (type.includes('LineString')) return { ...base, color: '#3b82f6' }
  return { ...base, color: '#a1a1aa' }
}

export default function FileDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const [file, setFile] = useState<FileDetail | null>(null)
  const [error, setError] = useState('')
  const [measurements, setMeasurements] = useState<MeasurementsResponse | null>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  useEffect(() => {
    let active = true
    params.then(async ({ id }) => {
      try {
        const data = await apiClient.getFile(id)
        if (active) setFile(data)
        try {
          const mdata = await apiClient.getMeasurements(id)
          if (active) setMeasurements(mdata)
        } catch {}
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load file.')
      }
    })
    return () => { active = false }
  }, [params])

  const features = measurements?.features || []
  const geoFeatures = features
    .filter(f => f.geometry && f.geometry.type)
    .map(f => ({ type: 'Feature' as const, geometry: f.geometry, properties: { ...f.properties, feature_index: f.feature_index, feature_id: f.feature_id, geometry_type: f.geometry_type, measurement_status: f.measurement_status } }))

  const hasGeometries = geoFeatures.length > 0
  const crsKind = file?.source_crs_kind

  return <main className="min-h-screen bg-[#07111f] px-5 py-8 text-slate-100 sm:px-8 lg:px-12">
    <div className="mx-auto max-w-6xl">
      <Link href="/" className="inline-flex items-center gap-2 text-sm text-slate-400 transition hover:text-sky-300"><ArrowLeft size={16} /> Back to workspace</Link>
      {error ? <section className="mt-8 rounded-2xl border border-red-400/20 bg-red-400/10 p-6 text-red-200"><h1 className="font-semibold">Could not load file</h1><p className="mt-2 text-sm text-red-200/80">{error}</p></section> : !file ? <div className="mt-16 flex items-center justify-center text-sm text-slate-400"><LoaderCircle className="mr-2 animate-spin" size={17} /> Loading file details</div> : <>
        <header className="mt-8 flex flex-col justify-between gap-4 border-b border-white/[.08] pb-7 sm:flex-row sm:items-end"><div><p className="text-xs uppercase tracking-[.2em] text-sky-400">File analysis</p><h1 className="mt-2 break-all text-3xl font-semibold tracking-tight">{nameOf(file)}</h1><p className="mt-2 text-sm text-slate-400">{formatOf(file)} · ID {file.id}</p></div><span className="rounded-full bg-emerald-400/10 px-3 py-1.5 text-xs text-emerald-300">{file.status || file.processing_status || 'Unknown status'}</span></header>
        <div className="mt-7 grid gap-4 sm:grid-cols-3"><Summary label="Features" value={numberOf(file.feature_count)} /><Summary label="Polygon area (m²)" value={numberOf(measurements?.summary?.total_area_square_meters ?? file.polygon_area)} /><Summary label="LineString length (m)" value={numberOf(measurements?.summary?.total_length_meters ?? file.linestring_length)} /></div>
        <div className="mt-6 grid gap-6 lg:grid-cols-[.8fr_1.2fr]"><section className="rounded-2xl border border-white/[.09] bg-[#0b1a2d] p-6"><h2 className="font-semibold">File information</h2><dl className="mt-5 flex flex-col gap-4 text-sm"><Info label="Format" value={formatOf(file)} /><Info label="Uploaded" value={file.uploaded_at || file.created_at || 'Not available'} /><Info label="Size" value={file.file_size ? `${file.file_size} bytes` : file.size ? `${file.size} bytes` : file.size_bytes ? `${file.size_bytes} bytes` : 'Not available'} /><Info label="Source CRS" value={file.source_crs_name || file.source_crs || 'Not available'} />{(file.error || file.error_detail || file.processing_error?.message) && <Info label="Processing error" value={file.error || file.error_detail || file.processing_error?.message || ''} />}</dl></section>
        <section className="rounded-2xl border border-white/[.09] bg-[#0b1a2d] p-6 min-h-[420px]">
          <div className="flex items-center justify-between mb-3"><h2 className="font-semibold">Geometry preview</h2><span className="text-xs text-slate-500">{crsKind ? `CRS: ${crsKind}` : ''}</span></div>
          {!mounted ? <div className="flex h-[340px] items-center justify-center text-slate-500"><LoaderCircle className="mr-2 animate-spin" size={17} /> Loading map…</div> :
            !hasGeometries ? <div className="flex min-h-[340px] flex-col items-center justify-center text-center text-slate-500"><MapPinned size={28} /><p className="mt-3">No geometries available</p><p className="mt-1 text-xs">The file contains no mappable features or processing is incomplete.</p></div> :
            <div className="h-[340px] w-full rounded-xl overflow-hidden border border-white/[.08]">
              <MapContainer center={[0,0]} zoom={2} style={{ height: '100%', width: '100%' }}>
                <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <GeoJSON data={{ type: 'FeatureCollection', features: geoFeatures } as any} style={(feature) => styleByType((feature as any)?.properties?.geometry_type || '')} onEachFeature={(feature, layer) => {
                  const props = (feature as any).properties
                  const popup = `<div class="text-sm"><strong>Feature ${props.feature_index}</strong><br/>Type: ${props.geometry_type}<br/>ID: ${props.feature_id || '—'}</div>`
                  layer.bindPopup(popup)
                }} />
              </MapContainer>
            </div>}
          <div className="mt-3 flex items-center gap-4 text-[11px] text-slate-500"><span className="inline-flex items-center gap-1"><span className="h-3 w-3 rounded-sm bg-emerald-500/60" /> Polygon</span><span className="inline-flex items-center gap-1"><span className="h-3 w-3 rounded-sm bg-blue-500/60" /> Line</span></div>
        </section></div>
      </>}
    </div>
  </main>
}

function Summary({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-white/[.08] bg-[#0b1a2d] p-5"><p className="text-xs text-slate-500">{label}</p><p className="mt-3 text-2xl font-semibold text-white">{value}</p></div> }
function Info({ label, value }: { label: string; value: string }) { return <div className="flex items-start justify-between gap-4 border-b border-white/[.06] pb-3"><dt className="text-slate-500">{label}</dt><dd className="max-w-[65%] break-words text-right text-slate-200">{value}</dd></div> }

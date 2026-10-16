interface MetricCardProps {
  title: string
  value: string | number
  subtitle?: string
  className?: string
}

export default function MetricCard({ title, value, subtitle, className = '' }: MetricCardProps) {
  return (
    <div className={`bg-white rounded-xl border border-stone-200 p-5 ${className}`}>
      <p className="text-sm font-medium text-stone-500">{title}</p>
      <p className="mt-1 text-2xl font-bold text-stone-900">{value}</p>
      {subtitle && <p className="mt-1 text-sm text-stone-400">{subtitle}</p>}
    </div>
  )
}

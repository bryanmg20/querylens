// Estado de carga: el diseño final con cada bloque como una barra plana de su tamaño real.
export default function DiagnosticsSkeleton() {
  return (
    <div aria-hidden="true" className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="card p-6 lg:col-span-2">
          <div className="skeleton h-5 w-40" />
          <div className="mt-5 flex items-center gap-5">
            <div className="skeleton h-16 w-16 rounded-full" />
            <div className="space-y-2">
              <div className="skeleton h-6 w-56" />
              <div className="skeleton h-4 w-72" />
            </div>
          </div>
          <div className="skeleton mt-5 h-8 w-full" />
        </div>
        <div className="card p-6">
          <div className="skeleton h-5 w-24" />
          <div className="skeleton mt-4 h-9 w-20" />
          <div className="skeleton mt-4 h-3 w-full" />
          <div className="skeleton mt-4 h-20 w-full" />
        </div>
      </div>
      <div className="card p-6">
        <div className="skeleton h-5 w-48" />
        <div className="skeleton mt-4 h-40 w-full" />
      </div>
    </div>
  );
}

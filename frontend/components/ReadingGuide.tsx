"use client";

// Explicación de la pantalla para quien llega por primera vez. Se pliega con "Entendido" y se
// recuerda; el botón del encabezado la vuelve a abrir.
const POINTS = [
  {
    title: "Qué es un antipatrón",
    text: "Una consulta o un índice que gasta más recursos de los necesarios. QueryLens los detecta observando cómo se ejecutan tus consultas, sin tocar tu base de datos.",
  },
  {
    title: "Cómo se ordena la lista",
    text: "Primero va lo que más impacto tuvo: el tiempo de base de datos que costó el problema en esta sesión. La severidad (alta, media, baja) dice cuánto se aparta de la regla y desempata. Toca una severidad arriba para filtrar la lista.",
  },
  {
    title: "Qué hacer con cada hallazgo",
    text: "Cada tarjeta trae la evidencia, una recomendación y el riesgo de aplicarla. El estado de evaluación dice qué patologías no se pudieron revisar y cómo habilitarlas.",
  },
];

export default function ReadingGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <div className="reveal" data-open={open} id="reading-guide">
      <div className="overflow-hidden">
        <section className="card mt-6 p-6" aria-labelledby="guide-title">
          <div className="flex items-center justify-between gap-3">
            <h2 id="guide-title" className="card-title">
              Cómo leer esta página
            </h2>
            <button type="button" onClick={onClose} className="btn-outline h-8">
              Entendido
            </button>
          </div>
          <div className="mt-4 grid gap-6 md:grid-cols-3">
            {POINTS.map((point) => (
              <div key={point.title}>
                <h3 className="text-[13px] font-semibold">{point.title}</h3>
                <p className="mt-1.5 text-[13px] leading-5 text-muted">{point.text}</p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

import DatabaseForm from "@/components/DatabaseForm";

export default function Home() {
  return (
    <main className="min-h-screen px-4 py-10 sm:py-16">
      <div className="mx-auto w-full max-w-4xl">
        <header className="mb-10">
          <h1 className="text-2xl text-ink sm:text-3xl">Conectar base de datos</h1>
          <p className="mt-2 max-w-md text-sm text-graphite-600">
            Registra una base PostgreSQL para que QueryLens empiece a observar sus consultas.
          </p>
        </header>
        <DatabaseForm />
      </div>
    </main>
  );
}

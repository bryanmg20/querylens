import DatabaseForm from "@/components/DatabaseForm";

export default function ConnectPage() {
  return (
    <main className="mx-auto w-full max-w-[1320px] px-4 pb-24 pt-8 md:px-8 md:pb-12">
      <header>
        <p className="text-[13px] text-muted">
          <span className="text-fg">QueryLens</span>
          <span aria-hidden="true"> / </span>
          Nueva conexión
        </p>
        <h1 className="mt-2 text-[26px] font-semibold leading-8 tracking-[-0.01em]">Conectar base de datos</h1>
        <p className="mt-1 max-w-xl text-sm text-muted">
          Registra una base PostgreSQL o MySQL para que QueryLens empiece a observar sus consultas.
        </p>
      </header>
      <div className="mt-8">
        <DatabaseForm />
      </div>
    </main>
  );
}

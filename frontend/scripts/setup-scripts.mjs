// Los scripts de preparar la base viven en querylens_database/. La interfaz los ofrece para
// descargar desde public/preparar-base/ (el build de Docker del frontend solo ve esta carpeta).
//   node scripts/setup-scripts.mjs        copia los scripts a public/preparar-base/
//   node scripts/setup-scripts.mjs check  falla si alguna copia difiere del original
import { copyFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "..", "querylens_database");
const target = join(root, "public", "preparar-base");
const FILES = [
  "setup_target_postgres.sql",
  "setup_target_mysql.sql",
  "teardown_target_postgres.sql",
  "teardown_target_mysql.sql",
];

if (!existsSync(source)) {
  // Dentro del build de Docker no existe querylens_database: se usan las copias ya versionadas.
  console.log("querylens_database/ no está disponible: se dejan las copias de public/preparar-base/");
  process.exit(0);
}

if (process.argv[2] === "check") {
  const stale = FILES.filter((f) => {
    const copy = join(target, f);
    return !existsSync(copy) || readFileSync(copy, "utf8") !== readFileSync(join(source, f), "utf8");
  });
  if (stale.length) {
    console.error(`Desactualizados en public/preparar-base/: ${stale.join(", ")}. Corre: npm run sync:setup`);
    process.exit(1);
  }
  console.log("Los scripts de public/preparar-base/ coinciden con querylens_database/.");
} else {
  mkdirSync(target, { recursive: true });
  for (const f of FILES) copyFileSync(join(source, f), join(target, f));
  console.log(`Copiados ${FILES.length} scripts a public/preparar-base/`);
}

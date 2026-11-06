// Contenido educativo de cada antipatrón. Los códigos AP-0x y las reglas de detección salen
// del informe del proyecto y de detection_engine/anti_patterns; las consultas y cifras de los
// ejemplos son ilustrativas y así se rotulan donde se muestran.

export type VizConfig =
  | {
      kind: "scan";
      table: string;
      rows: number;
      matches: number;
      readBad: number;
      readFix: number;
      note?: { bad: string; fix: string };
    }
  | { kind: "writes"; index: string; sizeMb: number }
  | { kind: "baseline"; median: number; bad: number[]; fix: number[] }
  | { kind: "estimate"; actual: number; estBad: number; estFix: number; planBad: string; planFix: string }
  | { kind: "spill"; dataMb: number; memBad: number; memFix: number; tempBlocks: number };

export interface AntipatternDoc {
  id: string;
  code: string;
  title: string;
  family: string;
  tagline: string;
  summary: string;
  rule: string;
  what: string[];
  causes: { title: string; text: string }[];
  bad: string;
  fix: string;
  compare: { metric: string; unit: string; bad: number; fix: number };
  checklist: string[];
  caveat: string;
  evidence: [string, string][];
  viz: VizConfig;
}

export const ANTIPATTERN_DOCS: Record<string, AntipatternDoc> = {
  baseline_degradation: {
    id: "baseline_degradation",
    code: "AP-01",
    title: "Degradación frente a su histórico",
    family: "Cambio en el tiempo",
    tagline: "Hoy tarda el doble que de costumbre.",
    summary: "Una consulta que antes respondía rápido ahora tarda mucho más que su comportamiento habitual.",
    rule: "Al menos 30 ejecuciones en la ventana y una latencia media de 2 veces o más la mediana de las 10 ventanas previas.",
    what: [
      "QueryLens guarda cómo se comporta cada consulta ventana tras ventana. Cuando la latencia media de una consulta supera con claridad su comportamiento habitual, la marca como degradada.",
      "No compara con un valor fijo: compara la consulta consigo misma, contra la mediana de sus 10 ventanas anteriores.",
    ],
    causes: [
      { title: "La tabla creció o cambió", text: "Más filas, o una distribución distinta de los datos, vuelven más costoso el mismo plan." },
      { title: "Un plan de ejecución distinto", text: "Con estadísticas nuevas el motor puede elegir otro camino que resulta peor." },
      { title: "Estadísticas desactualizadas", text: "El optimizador decide con una foto vieja de los datos." },
      { title: "Más concurrencia o bloqueos", text: "Otras consultas compiten por los mismos recursos en esa ventana." },
    ],
    bad: "Antes   12,4 ms\nAhora   31,0 ms",
    fix: "EXPLAIN (ANALYZE, BUFFERS)\nSELECT ...;  -- compara con el plan anterior",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 31, fix: 12.4 },
    checklist: [
      "Compara el plan actual con el anterior usando EXPLAIN (ANALYZE, BUFFERS).",
      "Ejecuta ANALYZE sobre las tablas que intervienen.",
      "Revisa si cambió el volumen de datos o el patrón de uso.",
      "Busca bloqueos o consultas concurrentes en la misma ventana.",
    ],
    caveat:
      "Una consulta con pocas ejecuciones varía mucho por azar. Por eso se exigen al menos 30 ejecuciones en la ventana, un aumento de 1 ms o más y que la latencia sea 2 veces la mediana o más.",
    evidence: [
      ["Ejecuciones", "412"],
      ["Mediana previa", "12,4 ms"],
      ["Ventana actual", "31,0 ms"],
    ],
    viz: {
      kind: "baseline",
      median: 12.4,
      bad: [12.1, 12.8, 11.9, 12.6, 12.3, 13.1, 12.5, 12.2, 19.4, 27.8, 31.0, 30.2],
      fix: [12.1, 12.8, 11.9, 12.6, 12.3, 13.1, 12.5, 12.2, 19.4, 15.2, 12.9, 12.4],
    },
  },
  avoidable_full_scan: {
    id: "avoidable_full_scan",
    code: "AP-02",
    title: "Escaneo completo evitable",
    family: "Recorrido completo de la tabla",
    tagline: "Leer toda la tabla para quedarse con el 1 %.",
    summary: "El plan lee toda la tabla aunque el predicado es muy selectivo y entrar por un índice sería mucho más barato.",
    rule: "Full table scan sobre una tabla de 10.000 filas o más cuyo predicado deja pasar como máximo el 10 %.",
    what: [
      "El plan de ejecución lee la tabla entera aunque el filtro es muy selectivo: solo una fracción pequeña de las filas cumple la condición.",
      "Es un recorrido evitable: con un camino de acceso adecuado, el motor podría ir directo a esas filas en lugar de revisarlas todas.",
    ],
    causes: [
      { title: "No hay un camino de acceso adecuado", text: "Ningún índice cubre el filtro, así que el motor solo puede recorrer todo." },
      { title: "El índice no cubre el filtro completo", text: "Cubre una parte del predicado, pero no la suficiente para que el motor lo prefiera." },
      { title: "Estadísticas desactualizadas", text: "El motor cree que el filtro devuelve muchas filas y por eso elige recorrer todo." },
      { title: "Se piden más filas de las necesarias", text: "Sin un filtro más estricto o un límite, el recorrido parece razonable." },
    ],
    bad: "SELECT id FROM movimientos\nWHERE estado = 'pendiente';",
    fix: "CREATE INDEX idx_mov_pendientes\n  ON movimientos (id)\n  WHERE estado = 'pendiente';",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 96, fix: 2.1 },
    checklist: [
      "Crea un índice, o un índice parcial, que cubra el filtro.",
      "Actualiza las estadísticas con ANALYZE.",
      "Pide solo las columnas y filas que necesitas.",
      "Revisa el plan con EXPLAIN antes y después del cambio.",
    ],
    caveat:
      "Recorrer toda la tabla es lo correcto cuando la tabla es pequeña o el filtro devuelve gran parte de ella. QueryLens solo lo marca con 10.000 filas o más y un filtro que deja pasar el 10 % o menos.",
    evidence: [
      ["Plan", "Seq Scan en movimientos"],
      ["Filas vivas", "12.004.113"],
      ["Filas estimadas", "1.402"],
    ],
    viz: { kind: "scan", table: "movimientos", rows: 12004113, matches: 5, readBad: 12004113, readFix: 1402 },
  },
  non_sargable_predicate: {
    id: "non_sargable_predicate",
    code: "AP-03",
    title: "Predicado no sargable",
    family: "El índice no se puede usar",
    tagline: "Hay un índice, pero la consulta lo ignora.",
    summary: "Aplicar una función a la columna dentro del WHERE impide usar su índice, así que la base de datos revisa fila por fila.",
    rule: "El predicado del plan aplica una expresión a la columna en vez de compararla directamente.",
    what: [
      "«Sargable» viene de «search argument able»: un predicado que el motor puede resolver apoyándose en un índice. Si aplicas una función a la columna, como LOWER(email) o DATE(fecha), el índice guarda los valores originales, no los transformados.",
      "El motor tiene entonces que calcular la función fila por fila para comparar. El índice está ahí, pero no sirve.",
    ],
    causes: [
      { title: "Funciones sobre la columna", text: "LOWER, UPPER, SUBSTRING o DATE aplicadas a la columna anulan el índice normal." },
      { title: "Operaciones aritméticas", text: "Un filtro como precio * 1,19 > 100 obliga a calcular el valor de cada fila." },
      { title: "Conversiones de tipo implícitas", text: "Comparar un texto con un número hace que el motor convierta la columna entera." },
      { title: "Comodines al inicio", text: "Un LIKE '%texto' no puede ubicarse dentro de un índice ordenado." },
    ],
    bad: "SELECT * FROM clientes\nWHERE lower(email) = 'ana@ejemplo.co';",
    fix: "CREATE INDEX idx_clientes_email_lower\n  ON clientes (lower(email));",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 62.8, fix: 0.6 },
    checklist: [
      "Compara la columna sin transformarla y transforma el valor: WHERE email = lower($1).",
      "Si necesitas la función, crea un índice sobre la expresión: ON clientes (lower(email)).",
      "Evita conversiones implícitas: usa el mismo tipo que tiene la columna.",
      "Revisa los filtros con LIKE '%...': necesitan otro tipo de índice o de búsqueda.",
    ],
    caveat:
      "Si la tabla es pequeña, o el filtro no es selectivo, el motor puede preferir recorrerla de todos modos y el predicado no sargable casi no cuesta nada.",
    evidence: [
      ["Predicado", "lower(email) = 'ana@ejemplo.co'"],
      ["Índice sobre email", "no aplicable"],
    ],
    viz: {
      kind: "scan",
      table: "clientes",
      rows: 480000,
      matches: 2,
      readBad: 480000,
      readFix: 1,
      note: {
        bad: "idx_clientes_email existe, pero no coincide con lower(email)",
        fix: "El índice sobre lower(email) sí es aplicable",
      },
    },
  },
  missing_index: {
    id: "missing_index",
    code: "AP-04",
    title: "Índice ausente",
    family: "Recorrido completo de la tabla",
    tagline: "Leer 1.250.000 filas para devolver 3.",
    summary: "Una consulta recorre entera una tabla grande para encontrar pocas filas, porque la columna que filtra no tiene índice.",
    rule: "Escaneo completo sobre una tabla de 10.000 filas o más, con un predicado que deja pasar el 10 % o menos de la tabla.",
    what: [
      "Cuando una consulta filtra por una columna que no tiene índice, el motor no sabe dónde están las filas que busca. Para estar seguro, lee la tabla entera, fila por fila, y se queda solo con las que cumplen la condición.",
      "Con una tabla pequeña no se nota. Con millones de filas, cada ejecución lee casi todo para devolver unas pocas, y ese costo se repite cada vez que la aplicación hace la consulta.",
    ],
    causes: [
      { title: "No hay un índice útil para el filtro", text: "Ningún índice empieza por la columna del WHERE, así que no existe un atajo." },
      { title: "El índice no encaja con la consulta", text: "Cubre otras columnas, o las tiene en otro orden, y el filtro no puede apoyarse en él." },
      { title: "La tabla creció", text: "Una consulta barata con miles de filas se vuelve lenta con millones, sin que el código cambie." },
      { title: "El índice no llegó a producción", text: "Existe en desarrollo, pero nunca se aplicó en el entorno real." },
    ],
    bad: "SELECT * FROM pedidos\nWHERE cliente_id = 4021;",
    fix: "CREATE INDEX idx_pedidos_cliente\n  ON pedidos (cliente_id);",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 184.2, fix: 0.4 },
    checklist: [
      "Crea un índice sobre la columna que filtra, cuando la consulta es frecuente y selectiva.",
      "Escribe el predicado sobre la columna sin transformarla.",
      "Mantén las estadísticas al día con ANALYZE.",
      "Acepta el escaneo completo si la tabla es pequeña o el filtro devuelve gran parte de ella.",
    ],
    caveat:
      "Un escaneo completo es la mejor opción cuando la tabla es pequeña o el filtro devuelve gran parte de las filas. QueryLens solo lo marca con 10.000 filas o más y un filtro que deja pasar el 10 % o menos.",
    evidence: [
      ["Plan", "Seq Scan en pedidos"],
      ["Filas vivas", "4.213.880"],
      ["Filas estimadas", "38"],
    ],
    viz: { kind: "scan", table: "pedidos", rows: 1250000, matches: 3, readBad: 1250000, readFix: 3 },
  },
  unused_index: {
    id: "unused_index",
    code: "AP-05",
    title: "Índice sin uso",
    family: "Costo sin beneficio",
    tagline: "Se actualiza en cada escritura y nadie lo lee.",
    summary: "Un índice que nadie lee sigue ocupando espacio y retrasa cada INSERT, UPDATE y DELETE.",
    rule: "Cero lecturas del índice durante la ventana de estadísticas disponible, y sin aparecer en los planes reales.",
    what: [
      "Un índice acelera las lecturas, pero cada INSERT, UPDATE o DELETE también tiene que actualizarlo. Si ninguna consulta lo usa, solo se paga el costo.",
      "QueryLens lo detecta cuando el contador de lecturas del índice es cero en la ventana de estadísticas disponible, y comprueba que tampoco aparezca en los planes reales.",
    ],
    causes: [
      { title: "Se creó «por si acaso»", text: "Nadie midió si alguna consulta lo necesitaba." },
      { title: "La consulta que lo usaba ya no existe", text: "Una funcionalidad se retiró y el índice se quedó." },
      { title: "Otro índice lo cubre", text: "Uno con las mismas columnas, o un prefijo, hace redundante al más corto." },
      { title: "El esquema cambió", text: "Columnas o filtros que cambiaron dejaron al índice sin uso." },
    ],
    bad: "-- idx_pedidos_notas\n-- lecturas del índice: 0",
    fix: "DROP INDEX idx_pedidos_notas;",
    compare: { metric: "Tiempo medio de un INSERT", unit: "ms", bad: 3.1, fix: 2.2 },
    checklist: [
      "Confirma que las estadísticas cubran un período lo bastante largo.",
      "Verifica que ningún reporte o tarea periódica use el índice.",
      "Elimínalo con DROP INDEX, de preferencia en horario de baja carga.",
      "Si dudas, mide primero el efecto en un entorno de pruebas.",
    ],
    caveat:
      "Los índices que respaldan una clave primaria o una restricción UNIQUE no se pueden eliminar aunque no se lean. Y uno que se usa una vez al mes puede mostrar cero lecturas en una ventana corta.",
    evidence: [
      ["Lecturas", "0"],
      ["Tamaño", "412 MB"],
    ],
    viz: { kind: "writes", index: "idx_pedidos_notas", sizeMb: 412 },
  },
  disk_spill: {
    id: "disk_spill",
    code: "AP-07",
    title: "Derrame a disco",
    family: "Falta de memoria",
    tagline: "No cabe en memoria y escribe en disco.",
    summary: "La consulta no cabe en memoria y escribe datos temporales en disco, lo que la hace mucho más lenta.",
    rule: "El indicador de escritura temporal en disco de la consulta aumentó respecto a la ventana anterior.",
    what: [
      "Ordenar, agrupar o unir muchas filas necesita memoria de trabajo. Cuando no alcanza, el motor guarda resultados parciales en archivos temporales en disco, que es muchísimo más lento.",
      "QueryLens lo detecta cuando el indicador de escritura temporal de la consulta aumenta respecto a la ventana anterior.",
    ],
    causes: [
      { title: "Memoria de trabajo pequeña", text: "work_mem en PostgreSQL o tmp_table_size en MySQL no alcanzan para esa operación." },
      { title: "Ordenamientos sobre demasiadas filas", text: "Se ordena o agrupa un volumen mayor del que la consulta necesita." },
      { title: "Falta un índice que entregue los datos ordenados", text: "Sin él, el motor ordena todo en memoria y se desborda." },
      { title: "Se piden más columnas de las necesarias", text: "Cada fila ocupa más memoria de la que hace falta." },
    ],
    bad: "SELECT ... ORDER BY total DESC;\n-- temp_blks_written: 18.432",
    fix: "SET work_mem = '64MB';",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 2400, fix: 310 },
    checklist: [
      "Reduce las filas antes de ordenar o agrupar: filtra primero, limita después.",
      "Crea un índice que entregue los datos ya ordenados.",
      "Aumenta work_mem solo para esa consulta o sesión, no de forma global.",
      "Pide solo las columnas que necesitas.",
    ],
    caveat:
      "El indicador no mide lo mismo en cada motor: en PostgreSQL cuenta bloques temporales escritos y en MySQL, tablas temporales creadas en disco. Los ordenamientos que van a disco en MySQL todavía no se recolectan.",
    evidence: [
      ["Bloques temporales", "18.432"],
      ["Memoria de trabajo", "4 MB"],
    ],
    viz: { kind: "spill", dataMb: 18, memBad: 4, memFix: 64, tempBlocks: 18432 },
  },
  cardinality_misestimate: {
    id: "cardinality_misestimate",
    code: "AP-08",
    title: "Estimación de filas errada",
    family: "El optimizador se equivoca",
    tagline: "Esperaba 12 filas y llegaron 48.300.",
    summary: "El optimizador espera un número de filas muy distinto del real y elige un plan equivocado.",
    rule: "La razón entre filas estimadas y reales es de 10 o más, y alguno de los dos valores llega a 100 filas.",
    what: [
      "Antes de ejecutar, el optimizador estima cuántas filas producirá cada paso y elige el plan según esa estimación. Si la estimación está muy lejos de la realidad, el plan elegido puede ser el equivocado.",
      "QueryLens compara las filas estimadas del EXPLAIN con las filas reales por ejecución. Si difieren 10 veces o más, lo marca.",
    ],
    causes: [
      { title: "Estadísticas desactualizadas", text: "El optimizador decide con una foto vieja de los datos." },
      { title: "Columnas correlacionadas", text: "Asume que los filtros son independientes entre sí, y no lo son." },
      { title: "Distribución muy desigual", text: "Unos pocos valores concentran casi todas las filas." },
      { title: "Predicados difíciles de estimar", text: "Funciones o expresiones complejas que el optimizador no sabe evaluar." },
    ],
    bad: "Estimadas        12\nReales       48.300",
    fix: "ANALYZE pedidos;",
    compare: { metric: "Tiempo medio por ejecución", unit: "ms", bad: 840, fix: 35 },
    checklist: [
      "Ejecuta ANALYZE sobre las tablas del filtro.",
      "Sube el objetivo de estadísticas de la columna si la distribución es desigual.",
      "Crea estadísticas extendidas para columnas correlacionadas (PostgreSQL).",
      "Reescribe los predicados que el optimizador no puede estimar.",
    ],
    caveat:
      "Solo se compara la salida final del plan, no los pasos intermedios, y se exige una razón de 10 veces y que alguno de los dos valores llegue a 100 filas.",
    evidence: [
      ["Razón", "4.025 veces"],
      ["Tabla", "pedidos"],
    ],
    viz: {
      kind: "estimate",
      actual: 48300,
      estBad: 12,
      estFix: 46900,
      planBad: "Nested Loop",
      planFix: "Hash Join",
    },
  },
};

/** Orden por código AP-0x, el de la navegación del detalle. */
export const ANTIPATTERN_ORDER = [
  "baseline_degradation",
  "avoidable_full_scan",
  "non_sargable_predicate",
  "missing_index",
  "unused_index",
  "disk_spill",
  "cardinality_misestimate",
] as const;

/** Orden en que se presentan en la página de inicio. */
export const LANDING_ORDER = [
  "missing_index",
  "avoidable_full_scan",
  "non_sargable_predicate",
  "unused_index",
  "baseline_degradation",
  "cardinality_misestimate",
  "disk_spill",
] as const;

export function getDoc(id: string): AntipatternDoc | undefined {
  return ANTIPATTERN_DOCS[id];
}

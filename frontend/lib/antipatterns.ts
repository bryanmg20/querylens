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
  | { kind: "spill"; dataMb: number; memBad: number; memFix: number; tempBlocks: number }
  | { kind: "nplus1"; parent: string; child: string; rows: number }
  | { kind: "deadlock"; scenario: "order" | "conversion" | "range" | "unknown" }
  | { kind: "cascade"; sessions: number }
  | { kind: "amplify"; table: string; rows: number; modified: number };

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
  n_plus_one: {
    id: "n_plus_one",
    code: "AP-06",
    title: "Patrón N+1",
    family: "Demasiadas consultas",
    tagline: "Una consulta para la lista y otra por cada fila.",
    summary: "Una consulta se repite una vez por cada fila que devolvió otra, cuando podría traer todo junto.",
    rule: "Una consulta hija con 50 ejecuciones o más en la ventana, que filtra por igualdad sobre una clave foránea y cuyas ejecuciones coinciden (±20 %) con las filas que devolvió una consulta padre, de 2 filas o más por ejecución.",
    what: [
      "La aplicación lee una lista de registros con una consulta (la padre) y después lanza otra consulta (la hija) por cada uno de ellos para traer sus datos relacionados. Con N filas son N + 1 consultas.",
      "Cada consulta es rápida por separado, así que no se ve como lenta. El costo está en el viaje de ida y vuelta a la base, repetido N veces, y crece con el tamaño de la lista.",
      "QueryLens lo detecta sin usar el plan: busca una consulta cuyo número de ejecuciones en la ventana coincide con las filas que devolvió otra, unidas por una clave foránea.",
    ],
    causes: [
      { title: "Carga perezosa del ORM", text: "Al recorrer la lista, cada acceso a la relación dispara su propia consulta." },
      { title: "Un bucle que consulta por cada registro", text: "El código pide los datos relacionados dentro del ciclo en lugar de pedirlos juntos." },
      { title: "Falta de carga anticipada", text: "No se indicó que la relación se necesita, así que el ORM la trae bajo demanda." },
      { title: "Listas que crecieron", text: "Con 10 filas pasaba inadvertido; con 500 son 500 viajes a la base por petición." },
    ],
    bad: "SELECT id FROM clientes WHERE region = 3;\n-- y por cada cliente:\nSELECT * FROM pedidos WHERE cliente_id = $1;",
    fix: "SELECT c.id, p.*\nFROM clientes c\nJOIN pedidos p ON p.cliente_id = c.id\nWHERE c.region = 3;",
    compare: { metric: "Consultas por petición", unit: "consultas", bad: 51, fix: 1 },
    checklist: [
      "Trae las filas relacionadas con un JOIN en la consulta padre.",
      "O pídelas todas juntas con WHERE cliente_id IN (...) sobre los valores del padre.",
      "Si usas un ORM, activa la carga anticipada (eager loading o prefetch) para esa relación.",
      "Verifica en pg_stat_statements que las ejecuciones de la hija bajen a una por petición.",
    ],
    caveat:
      "Una página de detalle que lee un pedido y luego su cliente es un «1 + 1» normal: por eso el padre debe devolver 2 filas o más por ejecución. Tampoco se cuentan las lecturas con bloqueo (FOR UPDATE o FOR KEY SHARE), como el chequeo interno de claves foráneas de PostgreSQL. Con menos de 50 ejecuciones en la ventana no se marca.",
    evidence: [
      ["Ejecuciones de la hija", "1.240"],
      ["Filas del padre", "1.250"],
      ["Clave foránea", "fk_pedidos_cliente"],
    ],
    viz: { kind: "nplus1", parent: "clientes", child: "pedidos", rows: 24 },
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

  // Bloqueos. Los códigos CT-0x y las reglas salen de detection_engine/deadlocks; los ejemplos
  // de sentencias y cifras son ilustrativos.
  inconsistent_lock_order_deadlock: {
    id: "inconsistent_lock_order_deadlock",
    code: "CT-01",
    title: "Abrazo mortal por orden inconsistente",
    family: "Bloqueos",
    tagline: "Cada una espera lo que la otra ya tiene.",
    summary: "Dos transacciones toman los mismos recursos en orden opuesto y quedan esperándose mutuamente.",
    rule: "Un ciclo de 2 o más transacciones en el grafo de espera, donde cada una mantiene en modo exclusivo el recurso que la otra solicita, adquiridos en orden opuesto.",
    what: [
      "Una transacción bloquea la fila 1 y después pide la fila 2. Otra, al mismo tiempo, bloquea la fila 2 y después pide la fila 1. Ninguna puede avanzar y ninguna suelta lo que tiene hasta terminar.",
      "El motor lo detecta, revierte una de las dos (la víctima) y le devuelve un error de abrazo mortal. Esa transacción pierde su trabajo y la aplicación debe reintentarla.",
    ],
    causes: [
      { title: "Rutas que recorren los datos en distinto orden", text: "Una transferencia de A a B y otra de B a A actualizan las mismas filas en sentido contrario." },
      { title: "Sin una regla de orden común", text: "Cada parte del código decide por su cuenta qué fila toma primero." },
      { title: "Transacciones largas", text: "Mientras más tiempo se retienen los bloqueos, más probable es que otra transacción se cruce." },
      { title: "Varias tablas en la misma transacción", text: "Una ruta modifica pedidos y luego facturas, y otra lo hace al revés." },
    ],
    bad: "-- Transacción A          -- Transacción B\nUPDATE cuentas ... id = 1;  UPDATE cuentas ... id = 2;\nUPDATE cuentas ... id = 2;  UPDATE cuentas ... id = 1;",
    fix: "-- Las dos, en el mismo orden (id ascendente)\nUPDATE cuentas ... id = 1;\nUPDATE cuentas ... id = 2;",
    compare: { metric: "Abrazos mortales por hora", unit: "abrazos", bad: 12, fix: 0.5 },
    checklist: [
      "Define un orden único de acceso: por ejemplo, siempre por clave primaria ascendente.",
      "Si necesitas varias filas, bloquéalas todas al inicio con SELECT ... FOR UPDATE ordenado por esa clave.",
      "Mantén las transacciones cortas para reducir la ventana del ciclo.",
      "Haz que la aplicación reintente la transacción revertida al recibir el error de abrazo mortal.",
    ],
    caveat:
      "Todas las rutas que modifican esas tablas deben seguir el mismo orden, o el ciclo reaparece entre una ruta nueva y una vieja. Bloquear por adelantado alarga el tiempo que se retienen los bloqueos y puede aumentar la espera de otras sesiones. En PostgreSQL el informe no dice qué mantenía cada transacción, y eso queda anotado en la evidencia.",
    evidence: [
      ["Transacciones en el ciclo", "2"],
      ["Veces que ocurrió", "3"],
      ["Tabla", "cuentas"],
    ],
    viz: { kind: "deadlock", scenario: "order" },
  },
  lock_mode_conversion_deadlock: {
    id: "lock_mode_conversion_deadlock",
    code: "CT-02",
    title: "Abrazo mortal por conversión de modo",
    family: "Bloqueos",
    tagline: "Las dos leen juntas y las dos quieren escribir.",
    summary: "Varias transacciones leen la misma fila con FOR SHARE y después todas piden pasar a exclusivo.",
    rule: "Un ciclo en el que todas las transacciones mantienen FOR SHARE sobre el mismo recurso y piden exclusivo sobre él.",
    what: [
      "Un bloqueo compartido es compatible con otro compartido, así que las dos transacciones entran a leer la fila. Pero ninguna puede pasar a exclusivo mientras la otra conserve su lectura, y ninguna la suelta hasta terminar.",
      "A diferencia del orden inconsistente, aquí el recurso es el mismo: se espera por una fila que las dos ya tenían.",
    ],
    causes: [
      { title: "Leer con FOR SHARE y escribir después", text: "El patrón «comprobar y actualizar» toma la lectura compartida antes de saber que va a escribir." },
      { title: "Claves foráneas", text: "En MySQL el chequeo de una clave foránea toma un bloqueo compartido sobre la fila referenciada." },
      { title: "Lecturas en paralelo sobre filas calientes", text: "Muchas sesiones leen la misma fila y varias intentan modificarla." },
      { title: "Transacciones largas", text: "Mantener el bloqueo compartido mucho tiempo da margen para que otra se cruce." },
    ],
    bad: "-- Las dos transacciones:\nSELECT * FROM cuentas WHERE id = 1 FOR SHARE;\nUPDATE cuentas SET saldo = saldo - 10 WHERE id = 1;",
    fix: "-- Pedir exclusivo desde la lectura\nSELECT * FROM cuentas WHERE id = 1 FOR UPDATE;\nUPDATE cuentas SET saldo = saldo - 10 WHERE id = 1;",
    compare: { metric: "Abrazos mortales por hora", unit: "abrazos", bad: 9, fix: 0.5 },
    checklist: [
      "Si vas a escribir la fila, léela con FOR UPDATE desde el principio.",
      "Evita FOR SHARE cuando después se va a modificar el mismo registro.",
      "Mantén las transacciones cortas.",
      "Haz que la aplicación reintente la transacción revertida.",
    ],
    caveat:
      "FOR UPDATE pone en fila a las transacciones que antes leían en paralelo: las que solo leen y no modifican esperan más. En PostgreSQL el informe no dice qué mantenía cada transacción: la conversión se deduce de que todas esperaban la misma fila, y la evidencia lo marca como inferido. El informe muestra la sentencia que esperaba (el UPDATE), no la lectura con FOR SHARE, que ocurrió antes en la misma transacción.",
    evidence: [
      ["Transacciones en el ciclo", "2"],
      ["Veces que ocurrió", "5"],
      ["Tabla", "cuentas"],
    ],
    viz: { kind: "deadlock", scenario: "conversion" },
  },
  range_contention_deadlock: {
    id: "range_contention_deadlock",
    code: "CT-03",
    title: "Abrazo mortal por contención de rango",
    family: "Bloqueos",
    tagline: "Las dos bloquean el mismo hueco y ninguna puede insertar.",
    summary: "Dos transacciones bloquean el mismo hueco de un índice y después cada una intenta insertar en él.",
    rule: "Un ciclo con un bloqueo de intención de inserción esperando por un bloqueo de hueco o next-key sobre el mismo índice (MySQL).",
    what: [
      "En InnoDB, buscar una clave que no existe con FOR UPDATE bloquea el hueco donde esa clave estaría. Los bloqueos de hueco son compatibles entre sí: dos transacciones pueden tener el mismo hueco a la vez.",
      "El problema aparece al insertar: ninguna puede hacerlo mientras la otra mantenga el hueco, y ninguna lo suelta hasta terminar. El caso típico es «verificar que no existe y después insertar».",
    ],
    causes: [
      { title: "Verificar antes de insertar", text: "SELECT ... FOR UPDATE sobre una clave inexistente y luego INSERT de esa clave." },
      { title: "Aislamiento REPEATABLE READ", text: "InnoDB bloquea huecos y next-key para evitar lecturas fantasma." },
      { title: "Claves cercanas en el mismo hueco", text: "Dos sesiones buscan claves distintas que caen entre las mismas dos filas." },
      { title: "Inserciones al final del índice", text: "El hueco después de la última fila (supremum) lo comparte todo el que inserta ahí." },
    ],
    bad: "-- Las dos transacciones:\nSELECT * FROM reservas WHERE id = 15 FOR UPDATE;  -- no existe\nINSERT INTO reservas (id, ...) VALUES (15, ...);",
    fix: "INSERT INTO reservas (id, ...) VALUES (15, ...)\nON DUPLICATE KEY UPDATE ...;",
    compare: { metric: "Abrazos mortales por hora", unit: "abrazos", bad: 7, fix: 0.5 },
    checklist: [
      "Inserta directamente y deja que la restricción única detecte el duplicado.",
      "Usa INSERT ... ON DUPLICATE KEY UPDATE en lugar de comprobar antes.",
      "Si el nivel lo permite, considera READ COMMITTED para evitar bloqueos de hueco.",
      "Mantén las transacciones cortas y reintenta la revertida.",
    ],
    caveat:
      "Solo aplica a MySQL: PostgreSQL no tiene bloqueos de hueco y sus conflictos de rango terminan en un error de serialización, no en un abrazo mortal. El hueco se identifica por el registro que lo cierra, con su dirección física, porque QueryLens no guarda los valores de los registros.",
    evidence: [
      ["Transacciones en el ciclo", "2"],
      ["Veces que ocurrió", "2"],
      ["Tabla", "reservas"],
    ],
    viz: { kind: "deadlock", scenario: "range" },
  },
  lock_amplification_deadlock: {
    id: "lock_amplification_deadlock",
    code: "CT-04",
    title: "Ampliación de bloqueos por falta de índice",
    family: "Bloqueos",
    tagline: "Modifica 2 filas y bloquea 100.",
    summary: "Un UPDATE o DELETE sin índice utilizable bloquea muchas más filas de las que modifica y termina en un abrazo mortal.",
    rule: "Un ciclo donde un UPDATE o DELETE sin índice utilizable mantiene 10 veces más bloqueos de fila que filas modificadas (MySQL).",
    what: [
      "Sin un índice que le sirva, InnoDB recorre el índice primario completo, y en REPEATABLE READ bloquea cada fila que lee, no solo las que modifica.",
      "Con tantas filas bloqueadas, cualquier otra transacción que toque la tabla puede cerrar un ciclo con ella.",
    ],
    causes: [
      { title: "Falta un índice sobre el filtro", text: "El WHERE compara columnas que no encabezan ningún índice de la tabla." },
      { title: "Aislamiento REPEATABLE READ", text: "Bloquea con next-key todo lo que recorre para evitar lecturas fantasma." },
      { title: "UPDATE o DELETE amplios", text: "Un filtro poco selectivo recorre y bloquea gran parte de la tabla." },
      { title: "Transacciones largas", text: "Los bloqueos de todas esas filas se retienen hasta el final." },
    ],
    bad: "UPDATE pedidos SET estado = 'enviado'\nWHERE codigo = 'A-1042';  -- sin índice sobre codigo",
    fix: "CREATE INDEX idx_pedidos_codigo ON pedidos (codigo);",
    compare: { metric: "Filas bloqueadas por fila modificada", unit: "filas", bad: 50, fix: 1 },
    checklist: [
      "Crea un índice cuya primera columna sea la que filtra el UPDATE o DELETE.",
      "Verifica con EXPLAIN que la sentencia deje de recorrer toda la tabla.",
      "Mantén las transacciones cortas para soltar los bloqueos pronto.",
      "Reintenta la transacción revertida.",
    ],
    caveat:
      "Si la tabla ya tiene un índice que empieza por una de las columnas del filtro, la ampliación tiene otra causa (bloqueos tomados antes en la transacción, un índice poco selectivo) y no es este patrón. Las filas bloqueadas y las modificadas son de toda la transacción, no solo de la sentencia que esperaba. Solo aplica a MySQL, y el recorrido completo se infiere de los metadatos, no de un plan.",
    evidence: [
      ["Filas bloqueadas", "1.200"],
      ["Filas modificadas", "24"],
      ["Proporción", "50 veces"],
    ],
    viz: { kind: "amplify", table: "pedidos", rows: 100, modified: 2 },
  },
  long_transaction_cascade: {
    id: "long_transaction_cascade",
    code: "CT-05",
    title: "Cascada por transacción larga",
    family: "Bloqueos",
    tagline: "Una sesión olvidada detiene a todas las demás.",
    summary: "Una transacción abierta mucho tiempo bloquea a otras sesiones, que se acumulan esperando.",
    rule: "En dos muestras consecutivas, una transacción raíz con 30 s o más de antigüedad bloquea a 2 o más sesiones que esperan 5 s o más.",
    what: [
      "No es un abrazo mortal: la espera va en una sola dirección, así que el motor no la resuelve solo. PostgreSQL espera indefinidamente por defecto y MySQL hasta innodb_lock_wait_timeout (50 s).",
      "La raíz típica es una sesión «idle in transaction»: la aplicación abrió la transacción, modificó algo y no la cerró. Todo lo que toque esa fila queda en cola detrás de ella.",
    ],
    causes: [
      { title: "Transacción abierta y olvidada", text: "Falta un COMMIT o ROLLBACK, por un error o una excepción que no cerró la conexión." },
      { title: "Trabajo externo dentro de la transacción", text: "Se llama a otro servicio o se espera al usuario con la transacción abierta." },
      { title: "Sin tiempos límite", text: "lock_timeout y idle_in_transaction_session_timeout no están configurados." },
      { title: "Una fila muy disputada", text: "Muchas sesiones quieren modificar la misma fila, así que la cola crece rápido." },
    ],
    bad: "BEGIN;\nUPDATE cuentas SET saldo = saldo - 10 WHERE id = 1;\n-- la aplicación no hace COMMIT",
    fix: "SET idle_in_transaction_session_timeout = '15s';\n-- y cerrar siempre la transacción: COMMIT o ROLLBACK",
    compare: { metric: "Espera acumulada de las sesiones", unit: "s", bad: 220, fix: 18 },
    checklist: [
      "Busca la sesión raíz: la que lleva más tiempo en «idle in transaction» y bloquea a otras.",
      "Corrige la aplicación para que cierre siempre la transacción, incluso si hay un error.",
      "Configura idle_in_transaction_session_timeout y lock_timeout en PostgreSQL, o innodb_lock_wait_timeout en MySQL.",
      "Saca de la transacción las llamadas a servicios externos.",
    ],
    caveat:
      "Terminar la sesión raíz revierte toda su transacción: los cambios que llevaba se pierden y la aplicación recibe un error en esa conexión, así que debe estar preparada para reintentar. Bajar los tiempos límite hace que las sesiones en espera fallen antes, y un idle_in_transaction_session_timeout muy bajo corta también transacciones legítimamente largas.",
    evidence: [
      ["Sesiones bloqueadas", "5"],
      ["Espera acumulada", "220 s"],
      ["Tabla", "cuentas"],
    ],
    viz: { kind: "cascade", sessions: 5 },
  },
  unclassified_deadlock: {
    id: "unclassified_deadlock",
    code: "CT",
    title: "Abrazo mortal sin clasificar",
    family: "Bloqueos",
    tagline: "Hubo un ciclo, pero no se sabe qué esperaba cada una.",
    summary: "Se detectó un abrazo mortal, pero el informe no identifica qué recurso esperaba cada transacción.",
    rule: "Un ciclo detectado cuyo informe no trae el recurso que esperaba cada transacción, así que no se puede distinguir entre orden inconsistente (CT-01) y conversión de modo (CT-02).",
    what: [
      "Sin saber qué recurso esperaba cada transacción no se puede decir si tomaron recursos distintos en orden opuesto o si todas pedían el mismo que ya tenían compartido.",
      "QueryLens no adivina: informa el abrazo, el arreglo que cubre los dos casos y cómo averiguar cuál fue.",
    ],
    causes: [
      { title: "PostgreSQL no informa la fila que espera cada proceso", text: "Solo dice qué transacción la tiene, no cuál es la fila." },
      { title: "Sentencias preparadas", text: "Con valores como $1 no hay de dónde sacar la fila por la que se esperaba." },
      { title: "No se toca una sola fila por clave única", text: "La sentencia no identifica un único registro." },
      { title: "log_lock_waits desactivado", text: "Sin ese registro, el servidor no deja constancia de la fila de las esperas largas." },
    ],
    bad: "-- Informe del abrazo:\n-- transacción A espera a B\n-- transacción B espera a A\n-- recurso: desconocido",
    fix: "ALTER SYSTEM SET log_lock_waits = on;\nSELECT pg_reload_conf();",
    compare: { metric: "Abrazos que se pueden clasificar", unit: "%", bad: 10, fix: 90 },
    checklist: [
      "Activa log_lock_waits en el servidor: registra la fila de las esperas largas.",
      "O registra por un tiempo acotado las sentencias del rol de la aplicación.",
      "Aplica mientras tanto el arreglo que cubre los dos casos: orden único de acceso y FOR UPDATE desde la lectura.",
      "Haz que la aplicación reintente la transacción revertida.",
    ],
    caveat:
      "Un abrazo sin clasificar es un abrazo real: el motor revirtió una transacción y la aplicación perdió su trabajo. Lo que falta es saber por qué, no si ocurrió.",
    evidence: [
      ["Transacciones en el ciclo", "2"],
      ["Veces que ocurrió", "1"],
    ],
    viz: { kind: "deadlock", scenario: "unknown" },
  },
};

/** Las dos secciones de Diagnósticos, cada una en el orden de su código (AP-0x, CT-0x). */
export const ANTIPATTERN_GROUPS = [
  {
    key: "antipatterns",
    title: "Antipatrones",
    ids: [
      "baseline_degradation",
      "avoidable_full_scan",
      "non_sargable_predicate",
      "missing_index",
      "unused_index",
      "n_plus_one",
      "disk_spill",
      "cardinality_misestimate",
    ],
  },
  {
    key: "deadlocks",
    title: "Abrazos mortales",
    ids: [
      "inconsistent_lock_order_deadlock",
      "lock_mode_conversion_deadlock",
      "range_contention_deadlock",
      "lock_amplification_deadlock",
      "long_transaction_cascade",
      "unclassified_deadlock",
    ],
  },
] as const;

/** Todos los patrones, antipatrones primero. */
export const ANTIPATTERN_ORDER = ANTIPATTERN_GROUPS.flatMap((g) => g.ids) as readonly string[];

/** La sección a la que pertenece un patrón (la primera si no se reconoce). */
export function groupOf(id: string) {
  return ANTIPATTERN_GROUPS.find((g) => (g.ids as readonly string[]).includes(id)) ?? ANTIPATTERN_GROUPS[0];
}

/** Orden en que se presentan en la página de inicio. */
export const LANDING_ORDER = [
  "missing_index",
  "avoidable_full_scan",
  "non_sargable_predicate",
  "unused_index",
  "baseline_degradation",
  "cardinality_misestimate",
  "n_plus_one",
  "disk_spill",
] as const;

export function getDoc(id: string): AntipatternDoc | undefined {
  return ANTIPATTERN_DOCS[id];
}

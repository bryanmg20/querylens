// "Por qué importa": qué le cuesta a la base de datos cada patología si no se atiende. Una frase
// por patología, escrita para quien no es DBA; la definición larga está en el catálogo
// (lib/antipatterns.ts) y la explicación concreta de cada hallazgo la escribe el detector.
const IMPACT: Record<string, string> = {
  baseline_degradation:
    "La consulta ya es más lenta de lo normal. Si la causa sigue, la lentitud llega a quien la usa y se acumula con la carga.",
  avoidable_full_scan:
    "Leer toda la tabla cuesta más a medida que crece: la consulta se vuelve más lenta y compite por disco y memoria con las demás.",
  non_sargable_predicate:
    "El filtro impide usar el índice, así que el motor revisa fila por fila. Aunque la tabla sea pequeña hoy, el costo crece con ella.",
  missing_index:
    "Sin el índice, cada ejecución recorre más filas de las necesarias. El costo se repite en cada llamada y crece con los datos.",
  unused_index:
    "Un índice que no se lee igual se actualiza en cada escritura y ocupa espacio: cuesta sin dar nada a cambio.",
  n_plus_one:
    "Una consulta por cada fila del resultado multiplica los viajes a la base. Cada uno es rápido, pero juntos saturan las conexiones.",
  disk_spill:
    "Cuando la operación no cabe en memoria se escribe en disco temporal, que es mucho más lento y se nota en la latencia.",
  cardinality_misestimate:
    "El motor decide el plan con una estimación equivocada y puede elegir uno mucho peor. Es la causa común de consultas que de pronto se vuelven lentas.",
  inconsistent_lock_order_deadlock:
    "Una de las transacciones se cancela para romper el abrazo mortal: el usuario ve un error y el trabajo hecho se pierde.",
  lock_mode_conversion_deadlock:
    "Dos transacciones piden subir su bloqueo a la vez y se esperan entre sí. El motor cancela una y su trabajo se pierde.",
  range_contention_deadlock:
    "Los bloqueos de rango frenan inserciones que no tienen relación directa. El motor cancela una transacción cuando se cruzan.",
  lock_amplification_deadlock:
    "Una sentencia bloquea muchas más filas de las que cambia, así que otras transacciones esperan por filas que no necesitaban.",
  unclassified_deadlock:
    "El motor canceló una transacción para romper el abrazo mortal: el usuario ve un error y el trabajo hecho se pierde.",
  long_transaction_cascade:
    "Una transacción abierta mucho tiempo mantiene bloqueos y deja en fila a las demás: cada minuto se suma a la espera de todas.",
};

export function whyItMatters(antipatron: string): string | null {
  return IMPACT[antipatron] ?? null;
}

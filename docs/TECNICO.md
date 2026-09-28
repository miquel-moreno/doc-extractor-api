# Detalles técnicos

## Ponerlo en marcha

```bash
cp .env.example .env   # elige proveedor de LLM y añade tu clave
docker compose up --build
```

La API queda en http://localhost:8000 (documentación interactiva en `/docs`).

Desarrollo local:

```bash
make install   # dependencias + hooks de pre-commit
make dev       # API con recarga automática
make check     # lint + tipos + tests
make eval      # evaluación con un LLM real (solo en local)
uv run alembic upgrade head   # crea o actualiza las tablas (usa DATABASE_URL)
```

## Arquitectura

```
src/doc_extractor_api/
├── api/        # rutas HTTP (FastAPI) y middleware
├── core/       # configuración, logging JSON, errores
├── services/   # lógica de negocio, sin red: se prueba con tests unitarios
└── adapters/   # LLM, base de datos y APIs externas, detrás de interfaces
```

## Decisiones técnicas

| Decisión | Por qué |
|---|---|
| LLM detrás de una interfaz propia (`LLMClient`) | Cambiar de proveedor (OpenAI, Anthropic, Ollama local) es cambiar una variable de entorno. Los tests usan un cliente falso: el CI no gasta dinero ni necesita claves. |
| El LLM propone, las reglas deciden (`services/validation.py`) | El modelo puede equivocarse con un número. Reglas deterministas comprueban importes de línea, subtotal, total, NIF/NIE/CIF (dígito de control), fechas, campos obligatorios y confianza. Cualquier incidencia marca el documento como `needs_review`: nunca se acepta un dato dudoso en silencio. |
| Importes con `Decimal`, no `float` | `0.1 + 0.2 != 0.3` en coma flotante. En facturas los importes tienen que ser exactos. |
| Tolerancia de 1 céntimo por línea y 2 en totales | Las facturas redondean línea a línea; exigir igualdad exacta mandaría a revisión documentos correctos. |
| Un único cliente HTTP compatible con Chat Completions (`adapters/llm.py`) | OpenAI y Ollama exponen la misma API, así que un cliente sirve para los dos: solo cambian URL, clave y modelo. Sin SDK del proveedor: menos dependencias y el mismo código para modelo local y de pago |
| Salida estructurada estricta (JSON schema, `strict: true`) | El modelo está obligado a devolver exactamente los campos del esquema. `strict_json_schema()` adapta el esquema de Pydantic a lo que exige el modo estricto (todo `required`, opcionales como `null`, sin `additionalProperties` ni `default`) |
| El LLM rellena un esquema intermedio sencillo (`LLMExtraction`) | Números normales, fecha como texto ISO y confianza con campos fijos: fácil de rellenar incluso para modelos pequeños. Nuestro código lo convierte al modelo de dominio con `Decimal` (vía `str`, para no arrastrar el error del `float`) |
| Un reintento con los errores | Si la respuesta no es JSON válido o no cumple el esquema o el modelo de dominio, se reenvía al modelo su respuesta y la lista de errores, y tiene una segunda oportunidad. Si vuelve a fallar, no se insiste: el documento irá a revisión |
| `temperature: 0` | Para extracción queremos la respuesta más probable y repetible, no creatividad |
| La confianza baja solo cuenta en campos con valor | Detectado en una prueba real: un albarán sin totales iba a revisión porque el modelo (correctamente) no estaba seguro de unos totales que no existen |
| Idempotencia por huella SHA-256 del contenido (`services/processing.py`) | El mismo documento devuelve siempre el mismo registro y el LLM solo se paga la primera vez. La columna `sha256` es `UNIQUE`: si dos copias llegan a la vez, la base de datos rechaza la segunda y se devuelve la primera |
| Nada se pierde | Si la extracción falla tras el reintento, el documento se guarda igualmente como `needs_review` con el motivo `extraction_failed`. Si el proveedor del LLM está caído, no se guarda nada, para poder reintentar después |
| SQLAlchemy 2 asíncrono + Alembic | Las llamadas al LLM son asíncronas; una base de datos síncrona bloquearía el servidor mientras espera. Alembic versiona la estructura de la base de datos como Git versiona el código |
| `asyncpg` para la aplicación, `psycopg` para las migraciones | psycopg en modo asíncrono no funciona con el bucle de eventos por defecto de Windows (`ProactorEventLoop`); se detectó probando contra un PostgreSQL real. asyncpg funciona igual en Windows y Linux. Las migraciones son un script síncrono y usan psycopg (`migrations/env.py` cambia el driver solo) |
| Tests con SQLite en memoria; producción con PostgreSQL | La CI no necesita un PostgreSQL. El modelo solo usa tipos portables (`JSON`, no `JSONB`), y un test aplica las migraciones y comprueba que el esquema resultante es idéntico al de los modelos |
| Casi todos los campos del esquema son opcionales | Si el LLM no encuentra un campo, el documento no se rechaza: la regla de campos obligatorios lo marca para revisión y la persona ve exactamente qué falta. |

## Datos de prueba

Todos los documentos son **sintéticos** (Faker `es_ES` + reportlab): empresas, personas, direcciones y NIF inventados.

```bash
uv run python -m scripts.generate_invoices   # regenera evals/dataset/ (semilla 1)
```

| Tipo | Nº | Variedad |
|---|---|---|
| Facturas (PDF) | 30 | 3 diseños: etiquetas distintas ("Base imponible" / "Subtotal" / "Importe neto"), fechas `13/07/2026`, `1 de septiembre de 2026` o `2026-07-13`, importes `1.234,56 €` o `EUR 1234.56`, NIF con o sin prefijo `ES` o guion, emisor en la cabecera o en el pie. IVA del 21, 10 o 4 % |
| Albaranes (PDF) | 10 | Sin precios, solo cantidades |
| Pedidos por email (texto) | 10 | Tres formatos de línea; la mitad sin CIF (el extractor debe dejarlo vacío, no inventarlo) |

Cada documento tiene su JSON esperado (`<id>.json`) y `manifest.json` los lista. Los tests comprueban que todos los JSON esperados pasan las reglas de negocio, que los PDF contienen los datos y que la misma semilla produce archivos idénticos byte a byte.

**Decisión:** el dataset se versiona en el repo (≈ 380 KB) en lugar de generarse al vuelo, para que cualquier evaluación, hoy o dentro de meses, se ejecute sobre exactamente los mismos documentos aunque cambie la versión de Faker.

## Evaluación

_Pendiente._ Resultados en `evals/results/`, con fecha y modelo.

## Limitaciones

- La confianza por campo la declara el propio modelo: es una señal útil pero no calibrada. Por eso nunca es la única defensa: las reglas de negocio se aplican siempre.
- Proveedor Anthropic todavía no implementado (la configuración lo contempla).
- PDFs escaneados (imagen sin texto) fuera del alcance actual: haría falta OCR.

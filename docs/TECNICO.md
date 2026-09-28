# Detalles técnicos

## Ponerlo en marcha

```bash
cp .env.example .env   # elige proveedor de LLM y añade tu clave
docker compose up --build
```

La API queda en http://localhost:8000 (documentación interactiva en `/docs`). `docker compose` levanta cuatro servicios: la API, el **worker** que procesa la cola, PostgreSQL y Redis. Al arrancar, el contenedor de la API aplica las migraciones pendientes. Con `LLM_PROVIDER=ollama`, el contenedor usa el Ollama de la máquina anfitriona (`host.docker.internal`).

## Página de demo

`http://localhost:8000/` sirve una página mínima (un solo HTML sin dependencias, `src/doc_extractor_api/static/index.html`) que llama a `POST /extract` y muestra la ficha con su estado. Es la del GIF del README, que se regenera con `uv run python -m scripts.record_demo` (Playwright con el Edge instalado; necesita el servicio en marcha y un LLM configurado).

## Uso de la API

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /extract` | Extrae y valida un documento: `file` (PDF con texto o texto UTF-8) **o** `text` (formulario) | `201` nuevo · `200` ya procesado (mismo contenido, sin llamar al LLM) · `413` > 10 MB · `415` ni PDF ni texto · `422` PDF ilegible o escaneado, o entrada vacía · `503` LLM caído o sin configurar |
| `POST /jobs` | Igual que `/extract` pero **en cola**: responde al instante con un número de trabajo y el worker lo procesa | `202` + cabecera `Location: /jobs/{id}` · mismos errores de entrada · `503` si Redis no está disponible |
| `GET /jobs/{id}` | Estado del trabajo (`queued`, `processing`, `done`, `failed`) y, cuando termina, el documento | `200` · `404` |
| `GET /documents/{id}` | Un documento procesado | `200` · `404` |
| `GET /reviews?limit=&offset=` | Bandeja de revisión humana (`needs_review`), más recientes primero | `200` |
| `GET /health` | Comprobación de vida | `200` |

```bash
# Una factura en PDF
curl -F "file=@evals/dataset/invoice-002.pdf;type=application/pdf" http://localhost:8000/extract

# El cuerpo de un email
curl --data-urlencode "text@evals/dataset/order-003.txt" http://localhost:8000/extract

# En cola: responde al instante; el resultado se consulta después
curl -F "file=@evals/dataset/invoice-005.pdf;type=application/pdf" http://localhost:8000/jobs
curl http://localhost:8000/jobs/<id>

# Lo que tiene que revisar una persona
curl http://localhost:8000/reviews
```

### Aviso al terminar (webhook)

Si `WEBHOOK_URL` está configurada, cuando un trabajo de la cola termina (`done` o `failed`) el worker envía un `POST` a esa dirección con el mismo trabajo que devuelve `GET /jobs/{id}`:

```json
{"event": "job.finished", "sent_at": "2026-09-28T14:30:00+00:00", "job": {"id": "…", "status": "done", "document": {"status": "valid", "data": {"total": "840.93", "…": "…"}}}}
```

Cabeceras: `X-Webhook-Signature: sha256=<HMAC-SHA256 del cuerpo con WEBHOOK_SECRET>`, `X-Webhook-Event: job.finished` y `X-Webhook-Id: <id del trabajo>` (para ignorar entregas repetidas). El receptor (por ejemplo, un flujo de n8n) debe calcular la firma sobre el **cuerpo en bruto** y compararla:

```python
import hashlib, hmac


def is_valid(raw_body: bytes, secret: str, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Si el receptor no responde `2xx`, el aviso se reintenta a los 5 s y a los 10 s (3 intentos) sin volver a procesar el documento.

Cada respuesta incluye los datos extraídos (`data`, importes como texto con 2 decimales), el estado (`valid` o `needs_review`), los motivos (`issues`), el modelo, los intentos, los tokens y la latencia.

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
├── adapters/   # LLM, base de datos, cola (Redis) y PDF, detrás de interfaces
└── worker.py   # worker de arq: procesa los trabajos de la cola
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
| Toda línea de factura debe tener precio unitario e importe | Detectado por la evaluación: el modelo local omitía el precio unitario y esas facturas pasaban como válidas porque la regla de línea solo se aplicaba con los dos valores presentes |
| La confianza baja solo cuenta en campos con valor | Detectado en una prueba real: un albarán sin totales iba a revisión porque el modelo (correctamente) no estaba seguro de unos totales que no existen |
| Idempotencia por huella SHA-256 del contenido (`services/processing.py`) | El mismo documento devuelve siempre el mismo registro y el LLM solo se paga la primera vez. La columna `sha256` es `UNIQUE`: si dos copias llegan a la vez, la base de datos rechaza la segunda y se devuelve la primera |
| Nada se pierde | Si la extracción falla tras el reintento, el documento se guarda igualmente como `needs_review` con el motivo `extraction_failed`. Si el proveedor del LLM está caído, no se guarda nada, para poder reintentar después |
| SQLAlchemy 2 asíncrono + Alembic | Las llamadas al LLM son asíncronas; una base de datos síncrona bloquearía el servidor mientras espera. Alembic versiona la estructura de la base de datos como Git versiona el código |
| `asyncpg` para la aplicación, `psycopg` para las migraciones | psycopg en modo asíncrono no funciona con el bucle de eventos por defecto de Windows (`ProactorEventLoop`); se detectó probando contra un PostgreSQL real. asyncpg funciona igual en Windows y Linux. Las migraciones son un script síncrono y usan psycopg (`migrations/env.py` cambia el driver solo) |
| `201` / `200` para distinguir nuevo y repetido | Quien llama (por ejemplo n8n) sabe si el documento es nuevo sin mirar el cuerpo, y reenviar el mismo archivo es seguro |
| Errores de entrada antes de llamar al LLM | Un PDF roto, escaneado, vacío o demasiado grande se rechaza con un código claro y sin gastar tokens |
| Cola con Redis + arq, y `/extract` se mantiene | Con muchos documentos a la vez, esperar la respuesta del LLM en cada petición bloquea conexiones y provoca timeouts. `POST /jobs` responde al instante (`202`) y un worker procesa en segundo plano. arq es asíncrono, como el resto del servicio (RQ es síncrono). `/extract` sigue disponible para un documento suelto |
| El estado del trabajo vive en PostgreSQL, Redis solo lleva el id | Si Redis se reinicia no se pierde el historial. Un trabajo terminado que llegue dos veces no se reprocesa. Si no se puede encolar, el trabajo se marca `failed` en vez de quedarse `queued` para siempre |
| Reintentos con espera creciente | Si el proveedor del LLM falla, el worker reintenta a los 10 s y a los 20 s (3 intentos en total) antes de marcar el trabajo como `failed` |
| La API conecta con Redis al primer uso | La API arranca y `/extract` funciona aunque Redis no esté; solo `/jobs` responde `503` |
| Webhook con URL fija y firmado con HMAC | La URL sale de la configuración, nunca de la petición: así nadie puede usar el servicio para llamar a direcciones internas (SSRF). La firma HMAC-SHA256 permite al receptor comprobar que el aviso viene de este servicio y no se ha modificado, como hacen Stripe o GitHub. El worker no arranca si hay URL pero no clave |
| El aviso es una tarea aparte en la cola | Si el receptor está caído, se reintenta solo el aviso: el documento no se vuelve a procesar ni a pagar |
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

```bash
make eval ARGS="--provider openai --model gpt-4.1-mini"
make eval ARGS="--provider ollama --model qwen2.5:3b"
```

Extracción + reglas de negocio sobre los 50 documentos sintéticos, comparando campo a campo con el JSON esperado (importes numéricos, NIF normalizado, textos sin distinguir mayúsculas ni espacios). Un documento es **perfecto** si todos los campos y todas las líneas coinciden. La métrica de **seguridad** es cuántos documentos con algún error se marcan como `valid` ("colados"): deberían ir todos a revisión. Resultados completos, documento a documento, en `evals/results/`.

**28/09/2026, dataset semilla 1:**

| | `gpt-4.1-mini` | `qwen2.5:3b` (Ollama, local, CPU) |
|---|---|---|
| Documentos perfectos | **49 / 50 (98 %)** | 9 / 50 (18 %) |
| Documentos con errores enviados a revisión | 1 de 1 | 37 de 41 |
| **Errores colados como `valid`** | **0** | 4 |
| Correctos enviados a revisión (falsas alarmas) | 0 | 1 |
| Acierto por campo (emisor · NIF · fecha · total · líneas) | 98 · 100 · 100 · 100 · 100 % | 78 · 78 · 96 · 92 · 36 % |
| Tiempo por documento (media) | 2,9 s | 24,2 s (Ryzen 7 5700U, sin GPU) |
| Coste de los 50 documentos | $0,036 (≈ $0,0007 por documento) | 0 |

- **El único error de `gpt-4.1-mini`** es un pedido de una persona autónoma sin CIF, firmado por otra persona: ambiguo incluso para un humano. El modelo declaró confianza baja y fue a revisión. No se ajustaron las instrucciones a ese caso para no sobreajustar al dataset.
- **La evaluación mejoró las reglas.** La primera ejecución con `qwen2.5:3b` dejaba pasar **24** documentos erróneos como `valid`: sobre todo facturas con líneas sin precio unitario (la regla solo comprobaba cantidad × precio cuando había precio) y números de documento con la etiqueta ("Factura nº …"). Se añadió la regla "toda línea de factura tiene precio e importe" y se aclaró el prompt; con eso los colados bajaron a **4** y los perfectos subieron de 3 a 9. `gpt-4.1-mini` quedó igual (49/50, 0 colados, 0 falsas alarmas). Ambas ejecuciones, antes y después, están en `evals/results/`.
- **Lo que las reglas no pueden detectar** (los 4 colados de `qwen2.5:3b`): fecha con día y mes intercambiados (las dos son fechas válidas), emisor confundido con otra empresa del documento, una línea mal leída en un pedido sin precios, y el caso ambiguo anterior. Son errores con datos coherentes entre sí: solo un modelo mejor (o la revisión humana por muestreo) los evita.
- Los modelos locales no son totalmente deterministas ni con `temperature: 0`; parte de la diferencia entre ejecuciones puede ser variación.

## Limitaciones

- Con un modelo pequeño local, algunos errores con datos coherentes (emisor confundido, día y mes intercambiados) pasan las reglas: ver la evaluación. Para producción se recomienda un modelo como `gpt-4.1-mini`.
- Si el mismo documento llega **dos veces a la vez** por la cola, el worker puede procesarlos en paralelo y llamar al LLM dos veces; solo se guarda un resultado (restricción `UNIQUE`), pero se paga la segunda llamada. Si la segunda copia llega cuando la primera ya terminó, no se vuelve a llamar al LLM.
- La confianza por campo la declara el propio modelo: es una señal útil pero no calibrada. Por eso nunca es la única defensa: las reglas de negocio se aplican siempre.
- Proveedor Anthropic todavía no implementado (la configuración lo contempla).
- PDFs escaneados (imagen sin texto) fuera del alcance actual: haría falta OCR.

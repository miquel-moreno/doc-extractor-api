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
| Casi todos los campos del esquema son opcionales | Si el LLM no encuentra un campo, el documento no se rechaza: la regla de campos obligatorios lo marca para revisión y la persona ve exactamente qué falta. |

## Evaluación

_Pendiente._ Resultados en `evals/results/`, con fecha y modelo.

## Limitaciones

_Pendiente._

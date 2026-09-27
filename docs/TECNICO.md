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

## Evaluación

_Pendiente._ Resultados en `evals/results/`, con fecha y modelo.

## Limitaciones

_Pendiente._

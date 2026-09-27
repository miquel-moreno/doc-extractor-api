# Facturas y pedidos en PDF convertidos en datos, sin teclear

**EN** · Turns invoices and orders (PDF or email) into validated data with AI, and flags anything that doesn't add up for human review.

Para empresas que reciben facturas, albaranes y pedidos por email y hoy los copian a mano en su programa de gestión.

> 🚧 **En desarrollo.** Primera versión prevista para octubre de 2026.

## Qué hace
- Lee una factura o un pedido (PDF o texto de un email) y devuelve sus datos ordenados: emisor, NIF, fecha, líneas, base, IVA y total
- Repasa las cuentas y el NIF; lo que no cuadra pasa a revisión humana en lugar de inventarse
- No procesa dos veces el mismo documento
- Avisa automáticamente al terminar, para conectarlo con herramientas como n8n

## Resultado
- Pendiente: se medirá con 50 facturas y albaranes de prueba generados automáticamente

## Tecnologías
Python · FastAPI · IA (LLM) · PostgreSQL · Redis · Docker · GitHub Actions

## Mi papel
Lo he diseñado y desarrollado de principio a fin. Desarrollo asistido por IA bajo mi especificación y revisión.

[Detalles técnicos →](docs/TECNICO.md) · [LinkedIn](https://www.linkedin.com/in/miquel-moreno-martinez)

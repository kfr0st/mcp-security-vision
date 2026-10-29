# Política de seguridad

## Versiones soportadas

| Versión | Soporte |
|---|---|
| `2.0.x` | ✅ Actualizaciones de seguridad |
| `< 2.0` | ❌ Sin soporte |

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de GitHub:

1. Entra en <https://github.com/kfr0st/mcp-security-vision/security/advisories>.
2. Pulsa **Report a vulnerability**.
3. Describe el problema con el mayor detalle posible.

También puedes usar **Security → Report a vulnerability** desde la pestaña del
repositorio. GitHub mantiene la conversación privada hasta que publiquemos el aviso.

### Qué incluir

- Versión del proyecto y del SDK MCP (`pip show mcp`).
- Sistema operativo y forma de despliegue (stdio, HTTP local, Docker, proxy…).
- Pasos mínimos para reproducir el problema.
- Impacto que crees que tiene (confidencialidad, integridad, disponibilidad).
- Prueba de concepto o logs **con credenciales enmascaradas**.

### Qué esperar

- Acuse de recibo en un plazo orientativo de 72 horas.
- Evaluación y plan de corrección en un plazo orientativo de 7 días.
- Crédito en el aviso de seguridad si lo deseas.

Este es un proyecto mantenido por voluntarios: los plazos son orientativos y no
constituyen un acuerdo de nivel de servicio.

## Modelo de seguridad y límites conocidos

- **El transporte HTTP (`sse` / `streamable-http`) no incluye autenticación.**
  Por defecto escucha en `127.0.0.1` y valida `Host`/`Origin` contra hosts locales
  para mitigar *DNS rebinding*. Si necesitas acceso remoto, ponlo detrás de un
  proxy con TLS y autenticación, o dentro de una VPN.
- Las credenciales de las cámaras deben vivir en `.env` o variables de entorno,
  nunca en el código ni en el historial de git.
- El reconocimiento facial trata datos biométricos. Eres responsable de informar
  a las personas y de cumplir la normativa aplicable (GDPR/RGPD, LFPDPPP,
  Ley 29733, etc.).
- `known_faces/`, `snapshots/` y `data/` contienen datos personales. No los subas
  al repositorio ni los compartas sin las garantías adecuadas.
- Las herramientas MCP se ejecutan con los permisos del proceso. Ejecuta el
  servidor con un usuario sin privilegios y limita el acceso al cliente MCP.

## Alcance

Se consideran vulnerabilidades, entre otras:

- Ejecución remota de código o escritura arbitraria de archivos.
- *Bypass* de la validación de `Host`/`Origin` o del enmascarado de credenciales.
- Inyección SQL en las consultas del historial.
- Escalada de privilegios en la imagen Docker.
- Exposición de datos biométricos o de credenciales en logs.

Quedan fuera del alcance:

- Debilidades que requieran acceso físico al equipo o a la red local.
- Falta de autenticación en el transporte HTTP cuando se expone deliberadamente
  sin proxy (está documentado como límite de diseño).
- Ataques de denegación de servicio triviales contra un servidor local.
- Vulnerabilidades en dependencias de terceros que ya tengan un aviso público;
  repórtalas aguas arriba y, si afectan a este proyecto, avísanos igualmente.

## Buenas prácticas para despliegues

1. Mantén `MCP_HOST=127.0.0.1` salvo que uses Docker/proxy.
2. Define `MCP_ALLOWED_HOSTS` y `MCP_ALLOWED_ORIGINS` si publicas detrás de un proxy.
3. Usa usuarios RTSP de solo lectura y contraseñas fuertes.
4. Segmenta las cámaras en una VLAN y limita su salida a Internet.
5. Rota los tokens de Docker Hub y de los webhooks.
6. Revisa la retención de `snapshots/` y `data/` (`cleanup_history`).

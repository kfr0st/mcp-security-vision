# known_faces/

Guarda aquí las fotos de las personas que quieres reconocer.

- Un archivo por persona, con el nombre en el nombre del archivo: `ana_torres.jpg`.
- El nombre visible se genera solo: `ana_torres.jpg` → **Ana Torres**.
- Requisitos de la foto: un **solo rostro**, de frente, bien iluminado y reciente
  (idealmente > 300 px de ancho). Si el sistema no detecta el rostro, lo avisará en `list_known_people`.
- Alternativa sin tocar archivos: pedir al agente `register_current_face` con la persona frente a la cámara.

Formatos admitidos: `.jpg`, `.jpeg`, `.png`, `.webp`.

> Por privacidad, esta carpeta está en `.gitignore`: las fotos **no** se suben al repositorio.

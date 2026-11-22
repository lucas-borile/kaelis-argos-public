# Integração RTSP/ONVIF

## Arquivos Criados/Modificados

- `backend/camera_gateway.py`: gateway independente para RTSP, ONVIF, perfis, snapshot e health check.
- `backend/main.py`: endpoints REST `/api/cameras`, sanitização de URLs com credenciais e integração com o cadastro existente.
- `backend/db.py`: metadados `camera_gateway_json` e campo reservado `camera_secret`.
- `backend/schemas.py`: modelos de request para câmera IP, descoberta ONVIF e teste de conexão.
- `backend/requirements-camera-gateway.txt`: dependências opcionais para ONVIF e criptografia de senha.

## Bibliotecas

- RTSP: `opencv-python-headless` via `cv2.VideoCapture`.
- ONVIF: `onvif-zeep`, quando instalado.
- Descoberta WS-Discovery: `WSDiscovery`, quando instalado.
- Senha persistida: `cryptography`, quando instalado e `ARGOS_CAMERA_SECRET_KEY` estiver configurada.

As dependências ONVIF são opcionais para não quebrar instalações que usam apenas streams públicos ou RTSP manual.

## Cadastro De Câmera RTSP

```http
POST /api/cameras
Authorization: Bearer TOKEN
Content-Type: application/json

{
  "name": "Portao principal",
  "rtsp_url_manual": "rtsp://usuario:senha@192.168.1.50:554/stream1",
  "lat": -23.5,
  "lon": -46.6,
  "azimuth_deg": 90
}
```

O cadastro cria uma câmera normal do Argos, então ela entra no pipeline existente de captura, YOLO, tracking e revisão.

## Teste RTSP

```http
POST /api/cameras/CAM_PORTAO_PRINCIPAL/test
Authorization: Bearer TOKEN
```

Resposta típica:

```json
{
  "status": "online",
  "online": true,
  "url": "rtsp://usuario:***@192.168.1.50:554/stream1",
  "fps": 25.0,
  "width": 1920,
  "height": 1080
}
```

## Descoberta ONVIF

Instale os extras:

```bash
pip install -r backend/requirements-camera-gateway.txt
```

Depois:

```http
POST /api/cameras/discover-onvif
Authorization: Bearer TOKEN
Content-Type: application/json

{
  "timeout_s": 6
}
```

## Perfis ONVIF

```http
GET /api/cameras/CAM_PORTAO_PRINCIPAL/profiles
Authorization: Bearer TOKEN
```

Quando a câmera expõe mais de um perfil, o gateway classifica perfis pequenos como `substream` e perfis maiores como `mainstream`. O cadastro via ONVIF prioriza `substream` quando houver URI RTSP disponível.

Para persistir senha ONVIF sem texto puro, configure:

```env
ARGOS_CAMERA_SECRET_KEY=uma-chave-longa-local
```

Sem essa variável, a senha não é persistida. Nesse caso, RTSP manual continua funcionando, mas consultas ONVIF futuras podem exigir atualizar a câmera com credenciais.

## Snapshot E Health

```http
POST /api/cameras/CAM_PORTAO_PRINCIPAL/snapshot
GET  /api/cameras/CAM_PORTAO_PRINCIPAL/health
```

## Limitações Conhecidas

- `cv2.VideoCapture` pode depender do backend FFmpeg disponível na instalação local.
- Descoberta ONVIF depende de multicast/WS-Discovery liberado na rede local.
- Alguns fabricantes exigem portas ONVIF diferentes de `80`.
- Se a URL RTSP manual contiver credenciais, a URL real precisa ficar disponível ao backend para captura; respostas públicas e logs mascaram a senha.
- Sem `ARGOS_CAMERA_SECRET_KEY` e `cryptography`, senhas ONVIF não são persistidas.

## Próximos Passos

- Exportar YOLO para TensorRT/ONNX em implantações edge.
- Usar FFmpeg subprocess com timeout rígido como alternativa ao `VideoCapture`.
- Adicionar seleção de perfil ONVIF pela UI.
- Separar credenciais em cofre externo ou KMS para produção.
- Registrar métricas históricas de saúde por câmera.

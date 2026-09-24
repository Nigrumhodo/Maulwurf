# NVIDIA Riva por gRPC

## Instalación realizada

El proyecto usa el cliente oficial del repositorio [nvidia-riva/python-clients](https://github.com/nvidia-riva/python-clients) mediante el paquete publicado en PyPI:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-riva.txt
```

Dependencia fijada:

- `nvidia-riva-client==2.27.0`
- `grpcio==1.84.0` y `grpcio-tools==1.81.1` quedaron instalados por resolución de dependencias en el entorno de validación.
- `protobuf==6.33.5`
- `websockets==15.0.1`

El cliente se importó correctamente con Python 3.14.7. El repositorio oficial se clonó temporalmente para auditar el código en el commit `54749aa2ff39a07f78e02c6525b7409bd2fe6ade`; no se copia ni se modifica dentro de este proyecto.

## Parámetros que usa el cliente oficial

El script auditado es `scripts/asr/transcribe_file_offline.py` y llama a `riva.client.Auth` y `ASRService.offline_recognize`.

### Conexión

Los parámetros de la llamada de NVIDIA son:

```text
--server grpc.nvcf.nvidia.com:443
--use-ssl
--metadata function-id b702f636-f60c-4a3d-a6f4-f3568c13bd7d
--metadata authorization "Bearer $NVIDIA_API_KEY"
```

El `authorization` debe construirse únicamente en el backend. La API key no debe guardarse en este repositorio ni enviarse desde el navegador.

El script transforma esos argumentos en:

```python
options = [
    ("grpc.max_receive_message_length", args.max_message_length),
    ("grpc.max_send_message_length", args.max_message_length),
]

auth = riva.client.Auth(
    use_ssl=args.use_ssl,
    uri=args.server,
    metadata_args=args.metadata,
    options=options,
)
```

`Auth` crea un canal gRPC TLS y reenvía el metadata a la llamada `Recognize`.

### Configuración ASR

`RecognitionConfig` recibe estos valores del script:

```python
riva.client.RecognitionConfig(
    language_code=args.language_code,
    model=args.model_name,
    max_alternatives=args.max_alternatives,
    profanity_filter=args.profanity_filter,
    enable_automatic_punctuation=args.automatic_punctuation,
    verbatim_transcripts=not args.no_verbatim_transcripts,
    enable_word_time_offsets=args.word_time_offsets or args.speaker_diarization,
)
```

Parámetros relevantes y valores por defecto del cliente:

| Parámetro | Por defecto | Observación |
|---|---:|---|
| `--language-code` | `""` | El cliente permite que el servidor infiera idioma si se omite. La aplicación debe exigirlo antes de subir audio, según `ESPECIFICACION.md`. |
| `--model-name` | `""` | El servidor selecciona el modelo si no se proporciona. El `function-id` identifica la función NVIDIA del ejemplo. |
| `--max-alternatives` | `1` | Número máximo de alternativas. |
| `--automatic-punctuation` | desactivado | Se envía como `enable_automatic_punctuation`. |
| `--word-time-offsets` | desactivado | Habilita offsets de palabras en la respuesta. No garantiza que el endpoint los entregue. |
| `--profanity-filter` | desactivado | Filtro de lenguaje obsceno. |
| `--no-verbatim-transcripts` | desactivado | Si se activa, solicita inverse text normalization. |
| `--custom-configuration` | vacío | Pares `key:value,key:value`; `task:translate` se usa para traducción, no para la transcripción normal de Maulwurf. |

La operación offline construye un único `RecognizeRequest` con:

```python
request = rasr.RecognizeRequest(config=config, audio=audio_bytes)
```

Y el archivo se carga completo en memoria antes de enviarse:

```python
with args.input_file.open("rb") as fh:
    data = fh.read()
```

Por tanto, el script offline no fragmenta un archivo grande. La alternativa streaming utiliza otra API y otro contrato.

## Límite de 1 GiB

El código actual de `argparse_utils.py` valida `--max-message-length` así:

- mínimo: `4 * 1024 * 1024` = **4 MiB**;
- valor por defecto: `64 * 1024 * 1024` = **64 MiB**;
- máximo aceptado por el cliente: `1024 * 1024 * 1024` = **1 GiB** (`1073741824` bytes).

Para solicitar el máximo local hay que indicarlo explícitamente:

```bash
--max-message-length 1073741824
```

Esto solo configura los límites de envío/recepción del canal gRPC del cliente. **No demuestra que el endpoint `grpc.nvcf.nvidia.com` acepte un archivo de 1 GiB.** El límite efectivo puede ser menor por:

- límite del servidor o de la función NVIDIA;
- tamaño serializado del `RecognizeRequest` (un archivo exactamente de 1 GiB también incluye configuración y overhead protobuf);
- proxy, cuota, deadline y memoria disponible;
- duración, formato, sample rate y modelo del audio.

Además, `fh.read()` implica que el proceso mantiene el archivo completo en RAM como `bytes`, aparte de buffers internos de gRPC y del servicio. El límite de archivo de la aplicación no debe fijarse en 1 GiB solo por esta constante del cliente.

## Comando de referencia para transcripción

Con el repositorio oficial clonado localmente y una API key configurada en el entorno:

```bash
.venv/bin/python python-clients/scripts/asr/transcribe_file_offline.py \
    --server grpc.nvcf.nvidia.com:443 \
    --use-ssl \
    --metadata function-id b702f636-f60c-4a3d-a6f4-f3568c13bd7d \
    --metadata authorization "Bearer $NVIDIA_API_KEY" \
    --max-message-length 1073741824 \
    --language-code en \
    --input-file /ruta/al/audio.wav
```

El ejemplo de NVIDIA recomienda audio mono de 16 bits en WAV, OPUS o FLAC. El formato de entrada de la aplicación no debe confundirse con el formato que finalmente se envía al endpoint: antes de integrar la ingesta hay que validar y normalizar con `ffprobe`/`ffmpeg` y probar sample rate, duración, idiomas y timestamps contra el endpoint real.

## Validación ejecutada

Se comprobó localmente:

- instalación de `nvidia-riva-client==2.27.0` dentro de `.venv`;
- import de `riva.client` y versión `2.27.0`;
- creación/introspección de las firmas `Auth` y `ASRService.offline_recognize`;
- `--help` del script offline;
- aceptación de `1073741824` por el validador de tamaño;
- código fuente del repositorio oficial en el commit indicado.

El 2026-09-24 (WSL) `scripts/provider/riva_spike.py` hizo un `Recognize` offline con audio sintético de S1.A1. El endpoint respondió gRPC `OK`. El cliente observado fue `nvidia-riva-client==2.27.0`, `grpcio==1.84.0`, `protobuf==6.33.5`. La metadata no trajo versión de servidor: queda `NO VERIFICADO`.

El mismo día, S1.A3 propuso la allowlist `es`, `en`, `fr` (gRPC `OK`, frase esperada no coincidente). `zz` y el idioma ausente respondieron `INVALID_ARGUMENT`. S1.A4 aceptó WAV PCM s16 mono en local. Con ffmpeg/ffprobe 8.0.1, mp3, ogg, opus, flac y webm se normalizan por pipe a WAV PCM s16 mono; m4a por pipe queda `NO VERIFICADO`. 16 kHz también respondió `OK` y no queda como límite aprobado; 22050 Hz es la observación de S1.A2.

S1.A5 midió tres llamadas en serie, sin reintentos. Una espera local de 0.05 s quedó como `DEADLINE_EXCEEDED` y no es deadline del servidor (`NO VERIFICADO`). Una cancelación inmediata respondió `CANCELLED`. Un WAV de 30.0 s a 16 kHz (960044 bytes) respondió `OK`: es un mínimo observado, no el máximo. 200 MiB y 3 h no se aprueban. Cuota, concurrencia y el límite efectivo de 1 GiB siguen `NO VERIFICADO`. La evidencia redactada está en la bitácora de Santiago. Timestamps y el informe S1.A8 siguen pendientes (S1.A6–A8).

El informe de esas pruebas (S1.A8) debe seguir el protocolo
[F0.1-protocolo.md](spike/F0.1-protocolo.md) (ticket L1.3). Otro formato no cierra F0.

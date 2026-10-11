# NVIDIA Riva over gRPC

## Installation performed

The project uses the official client from the [nvidia-riva/python-clients](https://github.com/nvidia-riva/python-clients) repository through the package published on PyPI:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-riva.txt
```

Pinned dependency:

- `nvidia-riva-client==2.27.0`
- `grpcio==1.84.0` and `grpcio-tools==1.81.1` were installed by dependency resolution in the validation environment.
- `protobuf==6.33.5`
- `websockets==15.0.1`

The client imported correctly with Python 3.14.7. The official repository was cloned
temporarily to audit the code at commit `54749aa2ff39a07f78e02c6525b7409bd2fe6ade`; it is
not copied into or modified within this project.

## Parameters used by the official client

The audited script is `scripts/asr/transcribe_file_offline.py`, and it calls `riva.client.Auth` and `ASRService.offline_recognize`.

### Connection

NVIDIA's call parameters are:

```text
--server grpc.nvcf.nvidia.com:443
--use-ssl
--metadata function-id b702f636-f60c-4a3d-a6f4-f3568c13bd7d
--metadata authorization "Bearer $NVIDIA_API_KEY"
```

The `authorization` must be built only in the backend. The API key must not be
stored in this repository or sent from the browser.

The script turns those arguments into:

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

`Auth` creates a TLS gRPC channel and forwards the metadata to the `Recognize` call.

### ASR configuration

`RecognitionConfig` receives these values from the script:

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

Relevant parameters and client defaults:

| Parameter | Default | Note |
|---|---:|---|
| `--language-code` | `""` | The client lets the server infer the language if omitted. The application must require it before uploading audio, per `ESPECIFICACION.md`. |
| `--model-name` | `""` | The server selects the model if none is provided. The `function-id` identifies the NVIDIA function from the example. |
| `--max-alternatives` | `1` | Maximum number of alternatives. |
| `--automatic-punctuation` | disabled | Sent as `enable_automatic_punctuation`. |
| `--word-time-offsets` | disabled | Enables word offsets in the response. It does not guarantee that the endpoint delivers them. |
| `--profanity-filter` | disabled | Profanity filter. |
| `--no-verbatim-transcripts` | disabled | If enabled, it requests inverse text normalization. |
| `--custom-configuration` | empty | `key:value,key:value` pairs; `task:translate` is used for translation, not for Maulwurf's normal transcription. |

The offline operation builds a single `RecognizeRequest` with:

```python
request = rasr.RecognizeRequest(config=config, audio=audio_bytes)
```

And the file is loaded entirely into memory before being sent:

```python
with args.input_file.open("rb") as fh:
    data = fh.read()
```

Therefore, the offline script does not fragment a large file. The streaming alternative
uses another API and another contract.

## The 1 GiB limit

The current code of `argparse_utils.py` validates `--max-message-length` as follows:

- minimum: `4 * 1024 * 1024` = **4 MiB**;
- default value: `64 * 1024 * 1024` = **64 MiB**;
- maximum accepted by the client: `1024 * 1024 * 1024` = **1 GiB** (`1073741824` bytes).

To request the local maximum it must be set explicitly:

```bash
--max-message-length 1073741824
```

This only configures the client gRPC channel's send/receive limits. **It does not
demonstrate that the `grpc.nvcf.nvidia.com` endpoint accepts a 1 GiB file.** The effective
limit may be lower due to:

- a server or NVIDIA function limit;
- the serialized size of the `RecognizeRequest` (a file of exactly 1 GiB also includes
  configuration and protobuf overhead);
- proxy, quota, deadline, and available memory;
- the audio's duration, format, sample rate, and model.

In addition, `fh.read()` means the process keeps the whole file in RAM as `bytes`, apart
from internal gRPC and service buffers. The application's file limit must not be set to
1 GiB just because of this client constant.

## Reference transcription command

With the official repository cloned locally and an API key configured in the environment:

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

NVIDIA's example recommends 16-bit mono audio in WAV, OPUS, or FLAC. The application's
input format must not be confused with the format finally sent to the endpoint: before
integrating ingestion, validate and normalize with `ffprobe`/`ffmpeg` and test sample rate,
duration, languages, and timestamps against the real endpoint.

## Validation performed

Verified locally:

- installation of `nvidia-riva-client==2.27.0` inside `.venv`;
- import of `riva.client` and version `2.27.0`;
- creation/introspection of the `Auth` and `ASRService.offline_recognize` signatures;
- the offline script's `--help`;
- acceptance of `1073741824` by the size validator;
- source code of the official repository at the indicated commit.

On 2026-09-24 (WSL) `scripts/provider/riva_spike.py` ran an offline `Recognize` with
synthetic audio from S1.A1. The endpoint answered gRPC `OK`. The observed client was
`nvidia-riva-client==2.27.0`, `grpcio==1.84.0`, `protobuf==6.33.5`. The metadata carried no
server version: it remains `NOT VERIFIED`.

The same day, S1.A3 proposed the allowlist `es`, `en`, `fr` (gRPC `OK`, expected sentence
not matching). `zz` and the missing language answered `INVALID_ARGUMENT`. S1.A4 accepted
WAV PCM s16 mono locally. With ffmpeg/ffprobe 8.0.1, mp3, ogg, opus, flac, and webm are
normalized by pipe to WAV PCM s16 mono; m4a by pipe remains `NOT VERIFIED`. 16 kHz also
answered `OK` and is not an approved limit; 22050 Hz is the S1.A2 observation.

S1.A5 measured three calls in series, without retries. A local 0.05 s wait resulted in
`DEADLINE_EXCEEDED` and is not a server deadline (`NOT VERIFIED`). An immediate cancellation
answered `CANCELLED`. A 30.0 s WAV at 16 kHz (960044 bytes) answered `OK`: it is an observed
minimum, not the maximum. 200 MiB and 3 h are not approved. Quota, concurrency, and the
effective 1 GiB limit remain `NOT VERIFIED`. The redacted evidence is in Santiago's logbook.

On 2026-09-27 (WSL) S1.A6 requested offsets with `enable_word_time_offsets` in two calls in
series, without retries. The installed client documents milliseconds (`Start (ms)` and
`start_time / 1000`, scale 0.001). Both answered gRPC `OK` with 0 words and 0 segments:
sentence 3.948 s, 174140 bytes, 22050 Hz, hypothesis 46 characters; silence added in RAM
5.948 s (spoken 3.948 s), 262340 bytes. D6 proposal `none`, status `pending`. It does not
close G2-T or G2-X.

The same day S1.A7 (`P-S1-SG-08`) measured three calls in series. Short `es`, 3.948 s:
latency 1.037 s, gRPC `OK`. The `es` 30.0 s baseline at 16 kHz, 960044 bytes: latency
1.692 s, gRPC `OK`. Short `en`, 3.231 s, 142510 bytes, 22050 Hz: latency 1.054 s, gRPC `OK`.
`en` at 30 s and `fr` remain `NOT VERIFIED`. `VmRSS` when importing `riva.client`:
20742144 bytes before and 36171776 after. The 3 h PCM at 16 kHz mono s16 is 345600000 bytes
(329.59 MiB); it was reserved in RAM and not uploaded. RSS before 36175872, peak 381779968,
delta 345604096. Cost per minute and per hour `NOT VERIFIED`. 200 MiB and 3 h are not
approved. Observed audio 37.178 s.

The S1.A8 report is in [spike/F0.1-informe-riva.md](spike/F0.1-informe-riva.md). The S1.A9
minutes are in [spike/F0.1-acta-decisiones.md](spike/F0.1-acta-decisiones.md): D2 and D6
`pending`, D3-Audio and D4 `blocked`. None is `approved`.

The report for those tests (S1.A8) must follow the
[F0.1-protocolo.md](spike/F0.1-protocolo.md) protocol (ticket L1.3). Another format does
not close F0.

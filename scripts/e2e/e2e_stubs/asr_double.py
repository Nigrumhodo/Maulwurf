#!/usr/bin/env python3
"""Riva SpeechRecognition gRPC double (TLS) for the E-S2-JF-06 smoke (J2.6).

This is a transport double, not a provider-contract artifact: it has no NVIDIA key, no
model and no retention. It exists so the real ``GrpcRivaTransport`` and the real ASR child
(ffmpeg fragments -> gRPC -> reconciliation -> commit) run inside compose on every MR
without cloud credentials. The transcript is synthetic and lives in this file; no audio is
read, stored or logged.

The server speaks TLS because ``riva_transport_from_env`` refuses plaintext. The smoke
generates an ephemeral CA and server certificate with ``scripts/e2e/gen_riva_tls.sh`` and
mounts them read-only at ``/e2e-tls``.

Run with ``python asr_double.py``; the ingest image already ships grpcio + the Riva protos.
"""

from __future__ import annotations

import os
import sys
from concurrent import futures

import grpc
from riva.client.proto import riva_asr_pb2 as rasr
from riva.client.proto import riva_asr_pb2_grpc as rasr_grpc

PORT = int(os.environ.get("PORT", "50051"))
TLS_DIR = os.environ.get("ASR_TLS_DIR", "/e2e-tls")

# Synthetic class text. It is not a real class, a real voice or PII; the smoke's question
# reuses its vocabulary so both retrieval branches return the chunk. The compose override
# passes the same value to the smoke driver, so there is one fixture, not two.
DEFAULT_TEXT = (
    "La fotosíntesis convierte la luz del sol en energía química. "
    "Las plantas usan clorofila."
)
TEXT = os.environ.get("SMOKE_EXPECTED_TEXT", DEFAULT_TEXT).strip() or DEFAULT_TEXT
_WORD_MS = 130
_WORD_GAP_MS = 10


def _words() -> list[rasr.WordInfo]:
    words: list[rasr.WordInfo] = []
    start_ms = 0
    for token in TEXT.split():
        end_ms = start_ms + _WORD_MS
        words.append(
            rasr.WordInfo(
                word=token,
                start_time=start_ms,
                end_time=end_ms,
                confidence=1.0,
            )
        )
        start_ms = end_ms + _WORD_GAP_MS
    return words


class Servicer(rasr_grpc.RivaSpeechRecognitionServicer):
    def Recognize(self, request: rasr.RecognizeRequest, context: grpc.ServicerContext) -> rasr.RecognizeResponse:  # noqa: E501 - proto signature
        if not request.audio:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "empty_audio")
        alternative = rasr.SpeechRecognitionAlternative(
            transcript=TEXT,
            confidence=1.0,
            words=_words(),
        )
        result = rasr.SpeechRecognitionResult(
            alternatives=[alternative], channel_tag=0, audio_processed=0.0,
        )
        return rasr.RecognizeResponse(results=[result])

    def StreamingRecognize(self, request_iterator: object, context: grpc.ServicerContext) -> None:  # noqa: E501 - proto signature
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "streaming_not_used")

    def GetRivaSpeechRecognitionConfig(self, request: object, context: grpc.ServicerContext) -> None:  # noqa: E501 - proto signature
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "not_used")


def _server_credentials() -> grpc.ServerCredentials:
    try:
        with open(os.path.join(TLS_DIR, "server.pem"), "rb") as cert_file:
            certificate = cert_file.read()
        with open(os.path.join(TLS_DIR, "server.key"), "rb") as key_file:
            private_key = key_file.read()
    except OSError as exc:
        raise SystemExit(f"asr-double: missing TLS material in {TLS_DIR}: {exc}") from None
    return grpc.ssl_server_credentials([(private_key, certificate)])


def main() -> int:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    rasr_grpc.add_RivaSpeechRecognitionServicer_to_server(Servicer(), server)
    server.add_secure_port(f"0.0.0.0:{PORT}", _server_credentials())
    server.start()
    sys.stderr.write(f"asr-double listening with TLS on {PORT}\n")
    server.wait_for_termination()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

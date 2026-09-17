"""Servicio de ingesta — dueño: Santiago (S1 spike, S2 pipeline).

Regla de oro: cero audio durable (disco, Redis, logs, cachés). Si algo persiste
audio, se bloquea la entrega entera (F0.2/G1).
"""

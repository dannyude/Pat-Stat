from collections import defaultdict
from typing import Dict, Set

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)

    def connect(self, websocket: WebSocket, patient_id: str):
        """Register an already-accepted WebSocket connection for a patient."""
        self._connections[patient_id].add(websocket)

    def disconnect(self, websocket: WebSocket, patient_id: str):
        self._connections[patient_id].discard(websocket)
        if not self._connections[patient_id]:
            del self._connections[patient_id]

    async def broadcast_to_patient(self, patient_id: str, data: dict):
        dead: Set[WebSocket] = set()
        for ws in list(self._connections.get(patient_id, set())):
            try:
                await ws.send_json(data)
            except (WebSocketDisconnect, RuntimeError):
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws, patient_id)

    def active_count(self, patient_id: str) -> int:
        return len(self._connections.get(patient_id, set()))

    def total_connections(self) -> int:
        return sum(len(v) for v in self._connections.values())


manager = ConnectionManager()


__all__ = ["ConnectionManager", "manager"]

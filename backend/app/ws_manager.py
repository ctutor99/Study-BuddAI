from typing import Dict, Set
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # map session_id -> set of websockets
        self.active: Dict[str, Set[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        conns = self.active.setdefault(session_id, set())
        conns.add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket):
        conns = self.active.get(session_id)
        if not conns:
            return
        conns.discard(websocket)
        if not conns:
            del self.active[session_id]

    async def broadcast(self, session_id: str, message: dict):
        conns = self.active.get(session_id, set())
        if not conns:
            return
        remove = []
        for ws in list(conns):
            try:
                await ws.send_json(message)
            except Exception:
                remove.append(ws)
        for ws in remove:
            self.disconnect(session_id, ws)

manager = ConnectionManager()
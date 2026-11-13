from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    db_path: str = "warehouse.db"
    chunk_size: int = 5000          # records per transaction (memory bound)
    force: bool = False             # re-process a file already loaded (replay-safe)
    fail_after_chunks: Optional[int] = None  # test hook: simulate a crash after N chunks

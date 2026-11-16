"""Atomic cross-host ad-ID claims. Both source workers use the same local SQLite file."""
import os
import sqlite3
import tempfile
import time
from pathlib import Path

DEFAULT = Path(tempfile.gettempdir()) / 'nardbon-ad-claims.sqlite3'


class Claims:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('AD_CLAIM_DB', DEFAULT))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute('CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL)')
        self.db.commit()

    def acquire(self, ad_id, owner):
        self.db.execute('BEGIN IMMEDIATE')
        row = self.db.execute('SELECT owner,state,at FROM claims WHERE id=?', (ad_id,)).fetchone()
        if row and row[1] == 'claimed' and row[2] < time.time() - 7200:
            self.db.execute('DELETE FROM claims WHERE id=?', (ad_id,))
            row = None
        if row and row[1] == 'unverified' and owner == 'tgadsspy':
            self.db.execute("UPDATE claims SET owner=?,state='claimed',at=? WHERE id=?", (owner,time.time(),ad_id))
            self.db.commit()
            return 'acquired'
        if row:
            self.db.commit()
            return row[1]
        self.db.execute('INSERT INTO claims VALUES(?,?,?,?)', (ad_id, owner, 'claimed', time.time()))
        self.db.commit()
        return 'acquired'

    def finish(self, ad_id, owner, verified):
        self.db.execute('BEGIN IMMEDIATE')
        if verified:
            state='unverified' if verified == 'unverified' else 'done'
            self.db.execute("UPDATE claims SET state=?,at=? WHERE id=? AND owner=? AND state='claimed'", (state,time.time(),ad_id,owner))
        else:
            self.db.execute("DELETE FROM claims WHERE id=? AND owner=? AND state='claimed'", (ad_id, owner))
        self.db.commit()

    def close(self):
        self.db.close()

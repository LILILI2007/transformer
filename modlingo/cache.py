"""Single-owner SQLite cache. No credentials or complete response payloads."""
import hashlib
import json
import sqlite3
from pathlib import Path
from .core import Project, validate_translation
from .syntax import PARSER_VERSION,parser_version

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

class Cache:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(directory) / 'translations.sqlite3'))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, target TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS migrations (path TEXT PRIMARY KEY, fingerprint TEXT);
          CREATE TABLE IF NOT EXISTS legacy (mod_id TEXT, context TEXT, source TEXT, target TEXT, PRIMARY KEY(mod_id,context,source));
          CREATE TABLE IF NOT EXISTS attempts (run TEXT, key TEXT, state TEXT, reason TEXT, PRIMARY KEY(run,key));
          CREATE TABLE IF NOT EXISTS baseline (folder TEXT, context TEXT, source TEXT, target TEXT, PRIMARY KEY(folder,context));
          CREATE TABLE IF NOT EXISTS fallbacks (mod_id TEXT, context TEXT, source TEXT, PRIMARY KEY(mod_id,context,source));
          CREATE TABLE IF NOT EXISTS whole_memory (key TEXT PRIMARY KEY, target TEXT, conflict INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS diagnostics (key TEXT PRIMARY KEY, code TEXT NOT NULL, reason TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, exhausted INTEGER NOT NULL DEFAULT 0);
        ''')

    @staticmethod
    def key(config, mod_id, context, source, glossary):
        return digest([config.endpoint(validate_credentials=False), config.provider, config.protocol, config.model, 'zh', mod_id, context, source, glossary, parser_version(source)])

    def get(self, key):
        row = self.db.execute('SELECT target FROM translations WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def memory_get(self,key):
        row=self.db.execute('SELECT target,conflict FROM whole_memory WHERE key=?',(key,)).fetchone()
        return row[0] if row and not row[1] else None

    def memory_blocked(self,key):
        row=self.db.execute('SELECT conflict FROM whole_memory WHERE key=?',(key,)).fetchone()
        return bool(row and row[0])

    def memory_put_many(self,rows):
        with self.db:
            self.db.executemany('''INSERT INTO whole_memory(key,target) VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET conflict=CASE WHEN whole_memory.target!=excluded.target
                THEN 1 ELSE whole_memory.conflict END''',rows)

    def put(self, key, target):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO translations VALUES (?,?)', (key, target))

    def mark(self, run, key, state, reason=''):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?)', (run, key, state, reason))

    def mark_many(self,run,keys,state,reason=''):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?)', ((run,key,state,reason) for key in keys))

    def diagnostic(self, key):
        row = self.db.execute('SELECT code,reason,attempts,exhausted FROM diagnostics WHERE key=?', (key,)).fetchone()
        return dict(zip(('code','reason','attempts','exhausted'), row)) if row else None

    def record_failure(self, keys, error, attempts=0, exhausted=False):
        with self.db:
            self.db.executemany('''INSERT INTO diagnostics VALUES (?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET code=excluded.code,reason=excluded.reason,
                attempts=diagnostics.attempts+excluded.attempts,exhausted=excluded.exhausted''',
                ((key,getattr(error,'code','unknown'),str(error),attempts,int(exhausted)) for key in keys))

    def put_many(self,run,results):
        results=list(results)
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO translations VALUES (?,?)', ((key,target) for key,source,target in results))
            self.db.executemany('INSERT OR REPLACE INTO attempts VALUES (?,?,?,?)', ((run,key,'completed','') for key,source,target in results))
            self.db.executemany('DELETE FROM diagnostics WHERE key=?', ((key,) for key,source,target in results))

    def migrate(self, path):
        path = Path(path)
        stat = path.stat()
        fingerprint = str(stat.st_size) + ':' + str(stat.st_mtime_ns)
        if self.db.execute('SELECT 1 FROM migrations WHERE path=? AND fingerprint=?', (str(path), fingerprint)).fetchone():
            return 0
        project = Project.load(path)
        rows = [(project.mod_id, key, entry.source, entry.target) for key, entry in project.entries.items()
                if entry.target and not entry.error and entry.translated_source == entry.source and not validate_translation(entry.source, entry.target)]
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO legacy VALUES (?,?,?,?)', rows)
            self.db.executemany('INSERT OR REPLACE INTO fallbacks VALUES (?,?,?)', ((project.mod_id,key,entry.source) for key,entry in project.entries.items() if entry.origin=='manual' and not entry.target.strip()))
            self.db.execute('INSERT OR REPLACE INTO migrations VALUES (?,?)', (str(path), fingerprint))
        return len(rows)

    def legacy(self, mod_id, context, source):
        row = self.db.execute('SELECT target FROM legacy WHERE mod_id=? AND context=? AND source=?', (mod_id, context, source)).fetchone()
        return row[0] if row else None

    def fallback(self,mod_id,context,source):
        return self.db.execute('SELECT 1 FROM fallbacks WHERE mod_id=? AND context=? AND source=?',(mod_id,context,source)).fetchone() is not None

    def close(self):
        self.db.close()

    def baseline(self, folder, context):
        return self.db.execute('SELECT source,target FROM baseline WHERE folder=? AND context=?', (str(folder),context)).fetchone()

    def installed(self, folder, context, source, target):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO baseline VALUES (?,?,?,?)', (str(folder),context,source,target))

    def installed_many(self,folder,entries):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO baseline VALUES (?,?,?,?)', ((str(folder),key,entry.source,entry.target) for key,entry in entries if entry.target and not entry.error))

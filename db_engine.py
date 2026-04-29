import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "videos_db.sqlite")

# 未来扩展新字段时在此追加（旧 DB 自动 ALTER TABLE）
_MIGRATION_COLUMNS = [
    ("view_count",           "INTEGER DEFAULT 0"),
    ("like_count",           "INTEGER DEFAULT 0"),
    ("duration_seconds",     "REAL DEFAULT 0"),
    ("content_type_tags",    "TEXT"),
    ("style_type_tags",      "TEXT"),
    ("country_region",       "TEXT"),
    ("main_content",         "TEXT"),
    ("core_highlights",      "TEXT"),
    ("key_shot_prompts_cn",  "TEXT"),
]


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_analysis (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            platform             TEXT NOT NULL,
            search_keyword       TEXT NOT NULL,
            video_title          TEXT,
            video_link           TEXT UNIQUE NOT NULL,
            local_path           TEXT,
            is_high_quality      BOOLEAN,
            low_quality_reason   TEXT,
            view_count           INTEGER DEFAULT 0,
            like_count           INTEGER DEFAULT 0,
            duration_seconds     REAL DEFAULT 0,
            content_type_tags    TEXT,
            style_type_tags      TEXT,
            country_region       TEXT,
            main_content         TEXT,
            core_highlights      TEXT,
            key_shot_prompts_cn  TEXT,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """为已有 DB 追加缺失字段（幂等，可安全重复执行）"""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(video_analysis)")
    existing = {row[1] for row in cursor.fetchall()}
    for col, defn in _MIGRATION_COLUMNS:
        if col not in existing:
            try:
                cursor.execute(f"ALTER TABLE video_analysis ADD COLUMN {col} {defn}")
                print(f"[DB] 新增字段: {col}")
            except Exception as e:
                print(f"[DB] 跳过 {col}: {e}")
    conn.commit()


def check_video_exists(video_link):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM video_analysis WHERE video_link = ?", (video_link,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def insert_video_record(data: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO video_analysis (
                platform, search_keyword, video_title, video_link, local_path,
                is_high_quality, low_quality_reason,
                view_count, like_count, duration_seconds,
                content_type_tags, style_type_tags, country_region,
                main_content, core_highlights, key_shot_prompts_cn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("platform"),             data.get("search_keyword"),
            data.get("video_title"),          data.get("video_link"),
            data.get("local_path"),
            data.get("is_high_quality"),      data.get("low_quality_reason"),
            data.get("view_count", 0),        data.get("like_count", 0),
            data.get("duration_seconds", 0),
            data.get("content_type_tags"),    data.get("style_type_tags"),
            data.get("country_region"),
            data.get("main_content"),         data.get("core_highlights"),
            data.get("key_shot_prompts_cn"),
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_all_records():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM video_analysis ORDER BY created_at DESC")
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records


init_db()

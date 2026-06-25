from __future__ import annotations
import logging
import os
import asyncio
from typing import Optional, Any, Dict
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, event
from sqlalchemy.engine import Engine

logger = logging.getLogger("agix.database_client")

class Base(DeclarativeBase):
    pass

class DatabaseClient:
    """
    SQLAlchemy-based database client for AGIX.
    Supports a Hybrid architecture:
    - Core Engine (SQLite): Light transactional state (contexts, agents)
    - Logs Engine (PostgreSQL/SQLite): Heavy telemetry (log_items)
    """
    _instances: Dict[int, "DatabaseClient"] = {}
    
    def __init__(self, database_url: Optional[str] = None):
        """
        Initialize the database client with a single engine for atomic consistency.
        """
        import json
        import os
        import sys
        
        # CRITICAL: Use /agix/tmp/ (persistent mounted volume) NOT /tmp/ (ephemeral container path)
        # /tmp/agix.db gets WIPED on docker compose down && up — data loss!
        # /agix/tmp/ survives all restart methods (restart, down/up, recreate)
        self.database_url = database_url or os.environ.get("DATABASE_URL") or "sqlite+aiosqlite:////agix/tmp/agix.db"
        
        # 1. UNIFIED ENGINE - Ensure async driver for PostgreSQL (Issue #842 fix)
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

        print(f"Connecting Database: {self.database_url.split('@')[-1]}", flush=True, file=sys.stderr)
        
        # Prepare engine arguments
        engine_args = {
            "echo": False,
            "pool_pre_ping": True,
            "future": True,
            "json_serializer": lambda obj: json.dumps(obj, ensure_ascii=False),
            "json_deserializer": json.loads,
        }
        
        # Only add pooling params for external DBs (PostgreSQL)
        if not self.database_url.startswith("sqlite"):
            engine_args["pool_size"] = 10
            engine_args["max_overflow"] = 20

        self.engine = create_async_engine(
            self.database_url,
            **engine_args
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Aliases for backward compatibility while refactoring PersistenceManager
        self.logs_engine = self.engine
        self.logs_session_factory = self.session_factory
        self.hybrid_mode = False

        # Set SQLite-specific pragmas on engine
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            if self.engine.url.drivername == "sqlite+aiosqlite":
                try:
                    dbapi_connection.execute("PRAGMA foreign_keys = ON")
                    dbapi_connection.execute("PRAGMA busy_timeout = 120000")
                    dbapi_connection.execute("PRAGMA journal_mode=WAL")
                    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
                    dbapi_connection.execute("PRAGMA cache_size=10000")
                    dbapi_connection.execute("PRAGMA journal_size_limit = 67108864")
                    # Use numeric value for auto_vacuum to be more robust across drivers
                    dbapi_connection.execute("PRAGMA auto_vacuum = 2") # 2 = INCREMENTAL
                except Exception as e:
                    logger.warning(f"Failed to set SQLite pragmas: {e}")

    @classmethod
    def get_instance(cls, database_url: Optional[str] = None) -> "DatabaseClient":
        """Get or create loop-local singleton instance of DatabaseClient."""
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
        except RuntimeError:
            loop_id = 0
            
        if loop_id not in cls._instances:
            cls._instances[loop_id] = cls(database_url)
        return cls._instances[loop_id]

    def get_session(self, target: str = "core") -> AsyncSession:
        """Get a new async session. Target is ignored in Unified mode."""
        return self.session_factory()

    async def init_db(self):
        """Create all tables defined in models with automatic repair logic."""
        from python.scripts.repair_database import repair_database
        
        # 1. PRE-CONNECTION REPAIR CHECK (Issue #576)
        if self.engine.url.drivername == "sqlite+aiosqlite":
            db_url = str(self.engine.url)
            if "sqlite" in db_url:
                db_path = db_url.split(":///")[-1]
                if os.path.exists(db_path):
                    success, message = repair_database(db_path)
                    if "Malformed database moved" in message:
                        logger.warning(f"Database repair triggered (Pre-init): {message}")
                        await self.engine.dispose()

        # 2. SCHEMA INITIALIZATION WITH RETRIES
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Use a standard connection to ensure conn is properly bound
                async with self.engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                    if self.engine.url.drivername == "sqlite+aiosqlite":
                        await self._apply_sqlite_pruning(conn)
                    if "postgresql" in str(self.engine.url):
                        await self._apply_postgres_pruning(conn)
                # No need for manual commit() with engine.begin()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Database initialization attempt {attempt + 1} failed: {e}. Retrying...")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"Final database initialization attempt failed: {e}")
                    raise e

        logger.info("Database initialized (schema created)")

    async def _apply_sqlite_pruning(self, conn):
        """Apply SQLite triggers for bloat prevention (Issue #377)."""
        await conn.execute(text("DROP TRIGGER IF EXISTS prune_tools_log"))
        await conn.execute(text("DROP TRIGGER IF EXISTS prune_tasks_history"))
        await conn.execute(text("DROP TRIGGER IF EXISTS prune_log_items"))
        await conn.execute(text("DROP TRIGGER IF EXISTS prune_contexts"))

        if "tools_log" in Base.metadata.tables:
            await conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS prune_tools_log AFTER INSERT ON tools_log
                WHEN (SELECT COUNT(*) FROM tools_log WHERE task_id = NEW.task_id) > 10000
                BEGIN
                    DELETE FROM tools_log WHERE task_id = NEW.task_id AND id NOT IN (
                        SELECT id FROM tools_log WHERE task_id = NEW.task_id ORDER BY timestamp DESC LIMIT 9000
                    );
                END;
            """))
        
        if "log_items" in Base.metadata.tables:
            await conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS prune_log_items AFTER INSERT ON log_items
                WHEN (SELECT COUNT(*) FROM log_items WHERE log_id = NEW.log_id) > 2000
                BEGIN
                    DELETE FROM log_items WHERE log_id = NEW.log_id AND id NOT IN (
                        SELECT id FROM log_items WHERE log_id = NEW.log_id ORDER BY no DESC LIMIT 1500
                    );
                END;
            """))

        if "contexts" in Base.metadata.tables:
            await conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS prune_contexts AFTER INSERT ON contexts
                WHEN (SELECT COUNT(*) FROM contexts) > 500
                BEGIN
                    DELETE FROM contexts WHERE id NOT IN (
                        SELECT id FROM contexts ORDER BY last_message DESC LIMIT 450
                    );
                END;
            """))

    async def _apply_postgres_pruning(self, conn):
        """Apply PostgreSQL triggers for bloat prevention using trigger functions."""
        # Migrate TIMESTAMP columns to TIMESTAMPTZ (Issue #738 fix)
        await self._migrate_timestamp_columns(conn)
        
        # Guard: only apply log_items pruning if the table exists
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'log_items'
            )
        """))
        log_items_exists = result.scalar()
        
        if not log_items_exists:
            logger.info("log_items table does not exist yet, skipping pruning setup")
            return
        
        # Add composite index for efficient ordering and pruning (Issue #377 Performance)
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_log_items_log_id_no ON log_items (log_id, no DESC)"))
        
        # PostgreSQL requires a function to be defined for triggers
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION prune_log_items_func() RETURNS TRIGGER AS $$
            DECLARE
                boundary_id INT;
            BEGIN
                -- Optimization: Find the ID of the 20,001st most recent item.
                SELECT id INTO boundary_id FROM log_items 
                WHERE log_id = NEW.log_id 
                ORDER BY no DESC 
                OFFSET 20000 LIMIT 1;
                
                IF boundary_id IS NOT NULL THEN
                    DELETE FROM log_items WHERE log_id = NEW.log_id AND id <= boundary_id;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))

        await conn.execute(text("DROP TRIGGER IF EXISTS trigger_prune_log_items ON log_items"))
        await conn.execute(text("""
            CREATE TRIGGER trigger_prune_log_items
            AFTER INSERT ON log_items
            FOR EACH ROW EXECUTE FUNCTION prune_log_items_func();
        """))
        logger.info("PostgreSQL pruning triggers and optimized index applied")

    async def _migrate_timestamp_columns(self, conn):
        """Migrate TIMESTAMP columns to TIMESTAMPTZ for asyncpg compatibility (Issue #738).
        
        asyncpg strictly rejects timezone-aware datetimes into TIMESTAMP WITHOUT TIME ZONE
        columns. Since all our Python defaults use datetime.now(timezone.utc), the columns
        must be TIMESTAMPTZ. This migration is idempotent — already-migrated columns are skipped.
        """
        migrations = [
            ("contexts", "created_at"),
            ("contexts", "last_message"),
            ("messages", "created_at"),
            ("shared_memory", "created_at"),
            ("shared_memory", "updated_at"),
        ]
        migrated = 0
        for table, column in migrations:
            try:
                # Check if column exists and its current type
                result = await conn.execute(text(f"""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = '{column}'
                """))
                row = result.fetchone()
                if row and row[0] == 'timestamp without time zone':
                    await conn.execute(text(f"""
                        ALTER TABLE {table} ALTER COLUMN {column}
                        TYPE TIMESTAMPTZ USING {column} AT TIME ZONE 'UTC'
                    """))
                    migrated += 1
                    logger.info(f"Migrated {table}.{column} from TIMESTAMP to TIMESTAMPTZ")
            except Exception as e:
                logger.warning(f"Could not migrate {table}.{column}: {e}")
        if migrated:
            logger.info(f"Migrated {migrated} datetime columns to TIMESTAMPTZ (Issue #738)")


    async def vacuum(self):
        """Perform VACUUM and ANALYZE on the database."""
        async with self.engine.begin() as conn:
            if self.engine.url.drivername == "sqlite+aiosqlite":
                await conn.execute(text("VACUUM"))
                await conn.execute(text("ANALYZE"))
                logger.info("Database VACUUM and ANALYZE completed")

    async def is_connected(self) -> bool:
        """Check if database is reachable."""
        try:
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False

    async def disconnect(self):
        """Close engine connections."""
        await self.engine.dispose()
        logger.info("Database disconnected")

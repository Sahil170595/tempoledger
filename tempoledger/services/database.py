"""Database layer for PostgreSQL."""

import logging
from typing import Any

import asyncpg
from asyncpg import Pool

from tempoledger.config import settings
from tempoledger.exceptions import DatabaseError
from tempoledger.services.retry import retry_database

logger = logging.getLogger(__name__)


class DatabaseService:
    """Database service for PostgreSQL operations."""

    def __init__(self, database_url: str | None = None):
        """
        Initialize database service.

        Args:
            database_url: Optional database URL (uses settings if not provided)
        """
        self.database_url = database_url or str(settings.database_url).replace("+asyncpg", "")
        self.pool: Pool | None = None

    async def connect(self):
        """Create database connection pool."""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=5,
                max_size=20,
                command_timeout=60,
            )
            logger.info("Database connection pool created")
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}", exc_info=True)
            raise DatabaseError(f"Database connection failed: {e}") from e

    async def disconnect(self):
        """Close database connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    @retry_database
    async def execute(self, query: str, *args) -> str:
        """
        Execute a query and return the result.

        Args:
            query: SQL query
            *args: Query parameters

        Returns:
            Result string (usually row count)

        Raises:
            DatabaseError: If execution fails
        """
        if not self.pool:
            raise DatabaseError("Database not connected. Call connect() first.")

        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(query, *args)
                return result
        except Exception as e:
            logger.error(f"Database query failed: {e}", exc_info=True)
            raise DatabaseError(f"Query execution failed: {e}") from e

    @retry_database
    async def fetch(self, query: str, *args) -> list[dict[str, Any]]:
        """
        Fetch rows from a query.

        Args:
            query: SQL query
            *args: Query parameters

        Returns:
            List of dictionaries representing rows

        Raises:
            DatabaseError: If execution fails
        """
        if not self.pool:
            raise DatabaseError("Database not connected. Call connect() first.")

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *args)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Database fetch failed: {e}", exc_info=True)
            raise DatabaseError(f"Fetch failed: {e}") from e

    @retry_database
    async def fetchrow(self, query: str, *args) -> dict[str, Any] | None:
        """
        Fetch a single row from a query.

        Args:
            query: SQL query
            *args: Query parameters

        Returns:
            Dictionary representing the row, or None if not found

        Raises:
            DatabaseError: If execution fails
        """
        if not self.pool:
            raise DatabaseError("Database not connected. Call connect() first.")

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, *args)
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Database fetchrow failed: {e}", exc_info=True)
            raise DatabaseError(f"Fetchrow failed: {e}") from e

    @retry_database
    async def fetchval(self, query: str, *args) -> Any:
        """
        Fetch a single value from a query.

        Args:
            query: SQL query
            *args: Query parameters

        Returns:
            Single value, or None if not found

        Raises:
            DatabaseError: If execution fails
        """
        if not self.pool:
            raise DatabaseError("Database not connected. Call connect() first.")

        try:
            async with self.pool.acquire() as conn:
                value = await conn.fetchval(query, *args)
                return value
        except Exception as e:
            logger.error(f"Database fetchval failed: {e}", exc_info=True)
            raise DatabaseError(f"Fetchval failed: {e}") from e

    async def create_schema(self):
        """Create database schema if it doesn't exist (fallback method).

        Note: This is a fallback method. In production, use Alembic migrations.
        """
        schema_sql = """
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL,
            message_content TEXT NOT NULL,
            scheduled_time TIMESTAMPTZ NOT NULL,
            actual_send_time TIMESTAMPTZ,
            delivery_status VARCHAR(20),
            scheduling_metadata JSONB,
            delivery_metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_session_schedule
            ON scheduled_messages(session_id, scheduled_time);
        CREATE INDEX IF NOT EXISTS idx_send_status
            ON scheduled_messages(delivery_status, actual_send_time);

        CREATE TABLE IF NOT EXISTS scheduling_events (
            id UUID PRIMARY KEY,
            session_id UUID NOT NULL,
            message_id UUID,
            event_type VARCHAR(50) NOT NULL,
            event_data JSONB NOT NULL,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_session_events
            ON scheduling_events(session_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_event_type
            ON scheduling_events(event_type, timestamp);
        """

        await self.execute(schema_sql)
        logger.info("Database schema created/verified (using fallback method)")


# Global database instance
db = DatabaseService()

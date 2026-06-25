from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from python.helpers.database_client import Base

class ContextSQL(Base):
    __tablename__ = "contexts"
    
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_message: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    type: Mapped[str] = mapped_column(Text, default="user")
    parent_id: Mapped[Optional[str]] = mapped_column(Text, index=True)
    project_name: Mapped[Optional[str]] = mapped_column(Text, index=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    output_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    
    logs: Mapped[List["LogSQL"]] = relationship("LogSQL", back_populates="context", cascade="all, delete-orphan")

class AgentSQL(Base):
    __tablename__ = "agents"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_id: Mapped[str] = mapped_column(Text, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    
    # Relationship with strict DB-level FK
    messages: Mapped[List["MessageSQL"]] = relationship(
        "MessageSQL", 
        back_populates="agent", 
        cascade="all, delete-orphan"
    )

class MessageSQL(Base):
    __tablename__ = "messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    ai: Mapped[bool] = mapped_column(Boolean)
    content: Mapped[Any] = mapped_column(JSON) # Could be dict or str
    summary: Mapped[Optional[str]] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    agent: Mapped["AgentSQL"] = relationship(
        "AgentSQL", 
        back_populates="messages"
    )

class LogSQL(Base):
    __tablename__ = "logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_id: Mapped[str] = mapped_column(Text, ForeignKey("contexts.id", ondelete="CASCADE"), index=True)
    guid: Mapped[Optional[str]] = mapped_column(Text, unique=True, index=True)
    progress: Mapped[Optional[str]] = mapped_column(Text)
    progress_no: Mapped[int] = mapped_column(Integer, default=0)
    
    context: Mapped["ContextSQL"] = relationship("ContextSQL", back_populates="logs")

class LogItemSQL(Base):
    __tablename__ = "log_items"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("logs.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    heading: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(Text)
    kvps: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    temp: Mapped[bool] = mapped_column(Boolean, default=False)

class SharedMemorySQL(Base):
    __tablename__ = "shared_memory"
    
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    meta_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    project_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)

# Index for searching project data efficiently
Index("idx_context_project", ContextSQL.project_name)
Index("idx_shared_memory_project", SharedMemorySQL.project_name)

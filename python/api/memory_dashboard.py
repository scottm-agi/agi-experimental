from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers.memory import Memory, get_existing_memory_subdirs, get_context_memory_subdir, abs_db_dir, reload as memory_reload
from python.helpers.projects import get_active_projects_list
from python.helpers import files
from python.models import ModelConfig, ModelType
from langchain_core.documents import Document
from python.agent import AgentContext
import os
import shutil
import json
from datetime import datetime


class MemoryDashboard(ApiHandler):

    async def process(self, input: dict, request: Request) -> dict | Response:
        try:
            action = input.get("action", "search")
            if action == "get_memory_subdirs":
                return await self._get_memory_subdirs()
            elif action == "get_current_memory_subdir":
                return await self._get_current_memory_subdir(input)
            elif action == "get_initial_data":
                return await self._get_initial_data(input)
            elif action == "search":
                return await self._search_memories(input)
            elif action == "delete":
                return await self._delete_memory(input)
            elif action == "bulk_delete":
                return await self._bulk_delete_memories(input)
            elif action == "update":
                return await self._update_memory(input)
            elif action == "reset_all":
                return await self._reset_all_memories(input)
            elif action == "get_statistics":
                return await self._get_statistics(input)
            elif action == "export_all":
                return await self._export_all_memories(input)
            elif action == "import_memories":
                return await self._import_memories(input)
            else:
                return {
                    "success": False,
                    "error": f"Unknown action: {action}",
                    "memories": [],
                    "total_count": 0,
                }

        except Exception as e:
            return {"success": False, "error": str(e), "memories": [], "total_count": 0}

    async def _delete_memory(self, input: dict) -> dict:
        """Delete a memory by ID from the specified subdirectory."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            memory_id = input.get("memory_id")

            if not memory_id:
                return {"success": False, "error": "Memory ID is required for deletion"}

            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)

            rem = await memory.delete_documents_by_ids([memory_id])

            if len(rem) == 0:
                return {
                    "success": False,
                    "error": f"Memory with ID '{memory_id}' not found",
                }
            else:
                return {
                    "success": True,
                    "message": f"Memory {memory_id} deleted successfully",
                }

        except Exception as e:
            return {"success": False, "error": f"Failed to delete memory: {str(e)}"}

    async def _bulk_delete_memories(self, input: dict) -> dict:
        """Delete multiple memories by IDs from the specified subdirectory."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            memory_ids = input.get("memory_ids", [])

            if not memory_ids:
                return {
                    "success": False,
                    "error": "No memory IDs provided for bulk deletion",
                }

            if not isinstance(memory_ids, list):
                return {
                    "success": False,
                    "error": "Memory IDs must be provided as a list",
                }

            # delete
            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            rem = await memory.delete_documents_by_ids(memory_ids)

            if len(rem) == len(memory_ids):
                return {
                    "success": True,
                    "message": f"Successfully deleted {len(memory_ids)} memories",
                }
            elif len(rem) > 0:
                return {
                    "success": True,
                    "message": f"Successfully deleted {len(rem)} memories. {len(memory_ids) - len(rem)} failed.",
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to delete any memories.",
                }

        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to bulk delete memories: {str(e)}",
            }

    async def _get_current_memory_subdir(self, input: dict) -> dict:
        """Get the current memory subdirectory from the active context."""
        try:
            # Try to get the context from the request
            context_id = input.get("context_id", None)
            if not context_id:
                # Fallback to default if no context available
                return {"success": True, "memory_subdir": "default"}

            context = AgentContext.use(context_id)
            if not context:
                return {"success": True, "memory_subdir": "default"}

            memory_subdir = get_context_memory_subdir(context)
            return {"success": True, "memory_subdir": memory_subdir or "default"}

        except Exception:
            return {
                "success": True,  # Still success, just fallback to default
                "memory_subdir": "default",
            }

    async def _get_initial_data(self, input: dict) -> dict:
        """Consolidate multiple initialization calls into one."""
        try:
            # 1. Get current subdir
            current_res = await self._get_current_memory_subdir(input)
            current_subdir = current_res.get("memory_subdir", "default")
            
            # 2. Get all subdirs
            subdirs_res = await self._get_memory_subdirs()
            subdirs = subdirs_res.get("subdirs", ["default"])
            
            # 3. Get initial search results for the current subdir
            # Use original search params or default
            search_input = input.copy()
            search_input["memory_subdir"] = current_subdir
            search_res = await self._search_memories(search_input)
            
            return {
                "success": True,
                "memory_subdir": current_subdir,
                "subdirs": subdirs,
                **search_res # Includes memories, total_count, etc.
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get initial data: {str(e)}"}

    async def _get_memory_subdirs(self) -> dict:
        """Get available memory subdirectories with project metadata."""
        try:
            # 1. Get raw directory names
            raw_subdirs = get_existing_memory_subdirs()
            
            # 2. Get projects metadata
            projects = get_active_projects_list()
            project_map = {p["name"]: p for p in projects}
            
            # 3. Enhance with metadata
            enhanced_subdirs = []
            for subdir_id in raw_subdirs:
                item = {
                    "id": subdir_id,
                    "title": subdir_id,
                    "color": "#6c757d" # Gray default
                }
                
                # Check if it's a project
                if subdir_id.startswith("projects/"):
                    project_name = subdir_id.replace("projects/", "")
                    if project_name in project_map:
                        project = project_map[project_name]
                        item["title"] = project.get("title") or project_name
                        item["color"] = project.get("color") or item["color"]
                elif subdir_id == "default":
                    item["title"] = "Default Memory"
                    item["color"] = "#4f46e5" # Indigo for default
                
                enhanced_subdirs.append(item)
                
            return {"success": True, "subdirs": enhanced_subdirs}
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to get memory subdirectories: {str(e)}",
                "subdirs": [{"id": "default", "title": "Default Memory", "color": "#4f46e5"}],
            }

    async def _search_memories(self, input: dict) -> dict:
        """Search memories in the specified subdirectory."""
        try:
            # Get search parameters
            memory_subdir = input.get("memory_subdir", "default")
            area_filter = input.get("area", "")  # Filter by memory area
            search_query = input.get("search", "")  # Full-text search query
            limit = input.get("limit", 100)  # Number of results to return
            threshold = input.get("threshold", 0.6)  # Similarity threshold

            # Issue #721: Support searching across ALL memory subdirectories
            if memory_subdir == "__all__":
                return await self._search_all_memories(input, area_filter, search_query, limit, threshold)

            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)

            memories = []

            if search_query:
                docs = await memory.search_similarity_threshold(
                    query=search_query,
                    limit=limit,
                    threshold=threshold,
                    filter=f"area == '{area_filter}'" if area_filter else "",
                )
                memories = docs
            else:
                # If no search query, get all memories from specified area(s)
                all_docs = memory.db.get_all_docs()
                for doc_id, doc in all_docs.items():
                    # Apply area filter if specified
                    if area_filter and doc.metadata.get("area", "") != area_filter:
                        continue
                    memories.append(doc)

                # sort by timestamp
                def get_sort_key(m):
                    timestamp = m.metadata.get("timestamp", "0000-00-00 00:00:00")
                    return timestamp

                memories.sort(key=get_sort_key, reverse=True)

                # Apply limit AFTER sorting to get the newest entries
                if limit and len(memories) > limit:
                    memories = memories[:limit]

            # Format memories for the dashboard
            formatted_memories = [self._format_memory_for_dashboard(m) for m in memories]

            # Get summary statistics
            total_memories = len(formatted_memories)
            knowledge_count = sum(
                1 for m in formatted_memories if m["knowledge_source"]
            )
            conversation_count = total_memories - knowledge_count

            # Get total count of all memories in database (unfiltered)
            total_db_count = len(memory.db.get_all_docs())

            return {
                "success": True,
                "memories": formatted_memories,
                "total_count": total_memories,
                "total_db_count": total_db_count,
                "knowledge_count": knowledge_count,
                "conversation_count": conversation_count,
                "search_query": search_query,
                "area_filter": area_filter,
                "memory_subdir": memory_subdir,
            }

        except Exception as e:
            return {"success": False, "error": str(e), "memories": [], "total_count": 0}

    async def _search_all_memories(self, input: dict, area_filter: str, search_query: str, limit: int, threshold: float) -> dict:
        """Issue #721: Search across ALL memory subdirectories and combine results."""
        try:
            all_subdirs = get_existing_memory_subdirs()
            all_memories = []
            total_db_count = 0
            seen_ids = set()

            for subdir_id in all_subdirs:
                try:
                    memory = await Memory.get_by_subdir(subdir_id, preload_knowledge=False)
                    
                    if search_query:
                        docs = await memory.search_similarity_threshold(
                            query=search_query,
                            limit=limit,
                            threshold=threshold,
                            filter=f"area == '{area_filter}'" if area_filter else "",
                        )
                        for doc in docs:
                            doc_id = doc.metadata.get("id", id(doc))
                            if doc_id not in seen_ids:
                                # Tag with source subdir for display
                                doc.metadata["source_subdir"] = subdir_id
                                all_memories.append(doc)
                                seen_ids.add(doc_id)
                    else:
                        all_docs = memory.db.get_all_docs()
                        total_db_count += len(all_docs)
                        for doc_id, doc in all_docs.items():
                            if area_filter and doc.metadata.get("area", "") != area_filter:
                                continue
                            if doc_id not in seen_ids:
                                doc.metadata["source_subdir"] = subdir_id
                                all_memories.append(doc)
                                seen_ids.add(doc_id)
                except Exception as subdir_err:
                    # Skip individual subdir failures, continue with others
                    print(f"[MemoryDashboard] Warning: Failed to search subdir '{subdir_id}': {subdir_err}")
                    continue

            # Sort by timestamp (newest first)
            all_memories.sort(
                key=lambda m: m.metadata.get("timestamp", "0000-00-00 00:00:00"),
                reverse=True
            )

            # Apply limit
            if limit and len(all_memories) > limit:
                all_memories = all_memories[:limit]

            # Format
            formatted_memories = [self._format_memory_for_dashboard(m) for m in all_memories]

            total_memories = len(formatted_memories)
            knowledge_count = sum(1 for m in formatted_memories if m["knowledge_source"])
            conversation_count = total_memories - knowledge_count

            return {
                "success": True,
                "memories": formatted_memories,
                "total_count": total_memories,
                "total_db_count": total_db_count,
                "knowledge_count": knowledge_count,
                "conversation_count": conversation_count,
                "search_query": search_query,
                "area_filter": area_filter,
                "memory_subdir": "__all__",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to search all directories: {str(e)}", "memories": [], "total_count": 0}

    def _format_memory_for_dashboard(self, m: Document) -> dict:
        """Format a memory document for the dashboard."""
        metadata = m.metadata
        return {
            "id": metadata.get("id", "unknown"),
            "area": metadata.get("area", "unknown"),
            "timestamp": metadata.get("timestamp", "unknown"),
            # "content_preview": m.page_content[:200]
            # + ("..." if len(m.page_content) > 200 else ""),
            "content_full": m.page_content,
            "knowledge_source": metadata.get("knowledge_source", False),
            "source_file": metadata.get("source_file", ""),
            "file_type": metadata.get("file_type", ""),
            "consolidation_action": metadata.get("consolidation_action", ""),
            "tags": metadata.get("tags", []),
            "metadata": metadata,  # Include full metadata for advanced users
        }

    async def _update_memory(self, input: dict) -> dict:
        try:
            memory_subdir = input.get("memory_subdir")
            original = input.get("original")
            edited = input.get("edited")

            if not memory_subdir or not original or not edited:
                return {"success": False, "error": "Missing required parameters"}

            doc = Document(
                page_content=edited["content_full"],
                metadata=edited["metadata"],
            )

            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            id = (await memory.update_documents([doc]))[0]
            doc = memory.get_document_by_id(id)
            formatted_doc = self._format_memory_for_dashboard(doc) if doc else None

            return {"success": formatted_doc is not None, "memory": formatted_doc}
        except Exception as e:
            return {"success": False, "error": str(e), "memory": None}

    async def _reset_all_memories(self, input: dict) -> dict:
        """Reset/delete all memories in the specified subdirectory."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            preserve_knowledge = input.get("preserve_knowledge", True)
            
            # Get the database directory
            db_dir = abs_db_dir(memory_subdir)
            
            if not os.path.exists(db_dir):
                return {"success": True, "message": "Memory directory does not exist, nothing to reset"}
            
            # Get memory instance to count before deletion
            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            all_docs = memory.db.get_all_docs()
            
            if preserve_knowledge:
                # Only delete conversation memories, keep knowledge
                conversation_ids = [
                    doc_id for doc_id, doc in all_docs.items()
                    if not doc.metadata.get("knowledge_source", False)
                ]
                deleted_count = len(conversation_ids)
                
                if conversation_ids:
                    await memory.delete_documents_by_ids(conversation_ids)
                
                return {
                    "success": True,
                    "message": f"Reset complete. Deleted {deleted_count} conversation memories. Knowledge preserved.",
                    "deleted_count": deleted_count
                }
            else:
                # Delete everything - remove the entire database directory
                total_count = len(all_docs)
                
                # Remove from memory index
                if memory_subdir in Memory.index:
                    del Memory.index[memory_subdir]
                
                # Delete the database files
                if os.path.exists(db_dir):
                    # Remove index.faiss and index.pkl files
                    for filename in ["index.faiss", "index.pkl", "embedding.json", "knowledge_import.json"]:
                        filepath = os.path.join(db_dir, filename)
                        if os.path.exists(filepath):
                            os.remove(filepath)
                
                # Force memory reload
                memory_reload()
                
                return {
                    "success": True,
                    "message": f"Full reset complete. Deleted {total_count} memories including knowledge.",
                    "deleted_count": total_count
                }
                
        except Exception as e:
            return {"success": False, "error": f"Failed to reset memories: {str(e)}"}

    async def _get_statistics(self, input: dict) -> dict:
        """Get detailed statistics about the memory database."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            
            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            all_docs = memory.db.get_all_docs()
            
            # Calculate statistics
            total_count = len(all_docs)
            knowledge_count = 0
            conversation_count = 0
            area_counts = {}
            oldest_timestamp = None
            newest_timestamp = None
            total_content_length = 0
            
            for doc_id, doc in all_docs.items():
                # Count by source type
                if doc.metadata.get("knowledge_source", False):
                    knowledge_count += 1
                else:
                    conversation_count += 1
                
                # Count by area
                area = doc.metadata.get("area", "unknown")
                area_counts[area] = area_counts.get(area, 0) + 1
                
                # Track timestamps
                timestamp = doc.metadata.get("timestamp", "")
                if timestamp and timestamp != "unknown":
                    if oldest_timestamp is None or timestamp < oldest_timestamp:
                        oldest_timestamp = timestamp
                    if newest_timestamp is None or timestamp > newest_timestamp:
                        newest_timestamp = timestamp
                
                # Sum content length
                total_content_length += len(doc.page_content)
            
            # Get database file size
            db_dir = abs_db_dir(memory_subdir)
            db_size_bytes = 0
            if os.path.exists(db_dir):
                for filename in os.listdir(db_dir):
                    filepath = os.path.join(db_dir, filename)
                    if os.path.isfile(filepath):
                        db_size_bytes += os.path.getsize(filepath)
            
            return {
                "success": True,
                "statistics": {
                    "total_memories": total_count,
                    "knowledge_memories": knowledge_count,
                    "conversation_memories": conversation_count,
                    "area_breakdown": area_counts,
                    "oldest_memory": oldest_timestamp,
                    "newest_memory": newest_timestamp,
                    "total_content_chars": total_content_length,
                    "avg_content_chars": total_content_length // total_count if total_count > 0 else 0,
                    "database_size_bytes": db_size_bytes,
                    "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
                    "memory_subdir": memory_subdir
                }
            }
            
        except Exception as e:
            return {"success": False, "error": f"Failed to get statistics: {str(e)}"}

    async def _export_all_memories(self, input: dict) -> dict:
        """Export all memories from the specified subdirectory."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            
            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            all_docs = memory.db.get_all_docs()
            
            # Format all memories for export
            memories = []
            for doc_id, doc in all_docs.items():
                memories.append({
                    "id": doc.metadata.get("id", doc_id),
                    "area": doc.metadata.get("area", "unknown"),
                    "timestamp": doc.metadata.get("timestamp", "unknown"),
                    "content": doc.page_content,
                    "knowledge_source": doc.metadata.get("knowledge_source", False),
                    "source_file": doc.metadata.get("source_file", ""),
                    "tags": doc.metadata.get("tags", []),
                    "metadata": doc.metadata
                })
            
            return {
                "success": True,
                "export_data": {
                    "export_timestamp": datetime.now().isoformat(),
                    "memory_subdir": memory_subdir,
                    "total_memories": len(memories),
                    "memories": memories
                }
            }
            
        except Exception as e:
            return {"success": False, "error": f"Failed to export memories: {str(e)}"}

    async def _import_memories(self, input: dict) -> dict:
        """Import memories from exported data."""
        try:
            memory_subdir = input.get("memory_subdir", "default")
            import_data = input.get("import_data", {})
            overwrite_existing = input.get("overwrite_existing", False)
            
            if not import_data or "memories" not in import_data:
                return {"success": False, "error": "Invalid import data format"}
            
            memories_to_import = import_data.get("memories", [])
            if not memories_to_import:
                return {"success": False, "error": "No memories to import"}
            
            memory = await Memory.get_by_subdir(memory_subdir, preload_knowledge=False)
            
            imported_count = 0
            skipped_count = 0
            error_count = 0
            
            for mem_data in memories_to_import:
                try:
                    # Create document from import data
                    content = mem_data.get("content", "")
                    if not content:
                        skipped_count += 1
                        continue
                    
                    # Build metadata
                    metadata = mem_data.get("metadata", {})
                    if not metadata:
                        metadata = {
                            "area": mem_data.get("area", "main"),
                            "knowledge_source": mem_data.get("knowledge_source", False),
                            "source_file": mem_data.get("source_file", ""),
                            "tags": mem_data.get("tags", []),
                            "imported": True,
                            "import_timestamp": datetime.now().isoformat()
                        }
                    else:
                        metadata["imported"] = True
                        metadata["import_timestamp"] = datetime.now().isoformat()
                    
                    # Check if memory with same ID exists
                    existing_id = mem_data.get("id", "")
                    if existing_id and not overwrite_existing:
                        existing = memory.db.get_by_ids([existing_id])
                        if existing:
                            skipped_count += 1
                            continue
                    
                    # Create and insert document
                    doc = Document(page_content=content, metadata=metadata)
                    await memory.insert_documents([doc])
                    imported_count += 1
                    
                except Exception as e:
                    error_count += 1
            
            return {
                "success": True,
                "message": f"Import complete. Imported: {imported_count}, Skipped: {skipped_count}, Errors: {error_count}",
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "error_count": error_count
            }
            
        except Exception as e:
            return {"success": False, "error": f"Failed to import memories: {str(e)}"}

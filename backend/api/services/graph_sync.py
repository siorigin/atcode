# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from core.config import settings
from graph.service import MemgraphIngestor
from loguru import logger


class SyncStatus(Enum):
    """Status of a sync operation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncJob:
    """Represents a sync job."""

    job_id: str
    project_name: str
    status: SyncStatus = SyncStatus.PENDING
    progress: int = 0
    message: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    nodes_synced: int = 0
    relationships_synced: int = 0
    error: str | None = None


class GraphSyncService:
    """
    Service to sync graph data from build instance to primary instance.

    Sync Strategy:
    1. Export project data from build instance using Cypher queries
    2. Clean project data on primary instance
    3. Import data to primary instance in batches
    4. Optionally clean build instance after sync
    """

    _instance: GraphSyncService | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._current_sync: SyncJob | None = None
        self._sync_history: list[SyncJob] = []
        self._lock = asyncio.Lock()
        self._initialized = True

        logger.info("GraphSyncService initialized")

    def is_build_instance_configured(self) -> bool:
        """Check if a separate build instance is configured."""
        return bool(settings.MEMGRAPH_BUILD_HOST)

    def get_build_ingestor(self) -> MemgraphIngestor:
        """Get ingestor for build instance."""
        if not self.is_build_instance_configured():
            raise ValueError("Build instance not configured")

        return MemgraphIngestor(
            host=settings.MEMGRAPH_BUILD_HOST,
            port=settings.MEMGRAPH_BUILD_PORT,
            batch_size=settings.MEMGRAPH_BATCH_SIZE,
        )

    def get_primary_ingestor(self) -> MemgraphIngestor:
        """Get ingestor for primary instance."""
        return MemgraphIngestor(
            host=settings.MEMGRAPH_HOST,
            port=settings.MEMGRAPH_PORT,
            batch_size=settings.MEMGRAPH_BATCH_SIZE,
        )

    async def sync_project(
        self,
        project_name: str,
        job_id: str | None = None,
        clean_after_sync: bool | None = None,
    ) -> SyncJob:
        """
        Sync a project from build instance to primary instance.

        Args:
            project_name: Name of the project to sync
            job_id: Optional job ID for tracking
            clean_after_sync: Whether to clean build instance after sync
                             (defaults to config setting)

        Returns:
            SyncJob with sync status and statistics
        """
        if not self.is_build_instance_configured():
            raise ValueError(
                "Build instance not configured. Set MEMGRAPH_BUILD_HOST in .env"
            )

        async with self._lock:
            if self._current_sync:
                raise ValueError(
                    f"Sync already in progress: {self._current_sync.project_name}"
                )

            job = SyncJob(
                job_id=job_id
                or f"sync_{project_name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                project_name=project_name,
                status=SyncStatus.RUNNING,
                started_at=datetime.now(UTC).isoformat(),
                message="Starting sync...",
            )
            self._current_sync = job

        try:
            # Run sync in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="graph_sync"
            ) as executor:
                await loop.run_in_executor(
                    executor,
                    lambda: self._do_sync(
                        job,
                        clean_after_sync
                        if clean_after_sync is not None
                        else not settings.MEMGRAPH_BUILD_KEEP_AFTER_SYNC,
                    ),
                )

            job.status = SyncStatus.COMPLETED
            job.completed_at = datetime.now(UTC).isoformat()
            job.message = f"Sync completed: {job.nodes_synced} nodes, {job.relationships_synced} relationships"
            logger.info(f"Sync completed for {project_name}: {job.message}")

        except Exception as e:
            job.status = SyncStatus.FAILED
            job.completed_at = datetime.now(UTC).isoformat()
            job.error = str(e)
            job.message = f"Sync failed: {e}"
            logger.error(f"Sync failed for {project_name}: {e}", exc_info=True)

        finally:
            async with self._lock:
                self._sync_history.append(job)
                if len(self._sync_history) > 20:
                    self._sync_history = self._sync_history[-20:]
                self._current_sync = None

        return job

    def _do_sync(self, job: SyncJob, clean_after_sync: bool) -> None:
        """
        Perform the actual sync operation (runs in thread pool).

        Sync strategy:
        1. Export all nodes and relationships for the project from build instance
        2. Clean the project on primary instance
        3. Import nodes first, then relationships
        4. Optionally clean build instance
        """
        project_name = job.project_name
        project_prefix = f"{project_name}."

        job.message = "Connecting to instances..."
        job.progress = 5

        with (
            self.get_build_ingestor() as build_ing,
            self.get_primary_ingestor() as primary_ing,
        ):
            # Step 1: Export nodes from build instance
            job.message = "Exporting nodes from build instance..."
            job.progress = 10

            nodes_query = """
            MATCH (n)
            WHERE n.qualified_name STARTS WITH $prefix OR n.qualified_name = $name
            RETURN labels(n) AS labels, properties(n) AS props
            """
            nodes = build_ing.fetch_all(
                nodes_query, {"prefix": project_prefix, "name": project_name}
            )
            logger.info(f"Exported {len(nodes)} nodes from build instance")

            # Step 2: Export relationships from build instance
            job.message = "Exporting relationships from build instance..."
            job.progress = 25

            rels_query = """
            MATCH (a)-[r]->(b)
            WHERE (a.qualified_name STARTS WITH $prefix OR a.qualified_name = $name)
              AND (b.qualified_name STARTS WITH $prefix OR b.qualified_name = $name)
            RETURN a.qualified_name AS from_qn, type(r) AS rel_type,
                   b.qualified_name AS to_qn, properties(r) AS props
            """
            rels = build_ing.fetch_all(
                rels_query, {"prefix": project_prefix, "name": project_name}
            )
            logger.info(f"Exported {len(rels)} relationships from build instance")

            # Step 3: Clean project on primary instance
            job.message = "Cleaning project on primary instance..."
            job.progress = 40

            primary_ing.clean_project(project_name)
            logger.info(f"Cleaned project {project_name} on primary instance")

            # Step 4: Import nodes to primary instance using UNWIND for batch efficiency
            job.message = "Importing nodes to primary instance..."
            job.progress = 50

            batch_size = 200  # Smaller batches to reduce CPU blocking
            write_delay_ms = 50  # Delay between batches to allow reads through

            # Group nodes by their label combination for efficient batch inserts
            nodes_by_labels: dict[str, list] = {}
            for node in nodes:
                labels = node["labels"]
                props = node["props"]
                if not labels or not props:
                    continue
                label_key = ":".join(sorted(labels))
                if label_key not in nodes_by_labels:
                    nodes_by_labels[label_key] = []
                nodes_by_labels[label_key].append(props)

            total_nodes = sum(len(n) for n in nodes_by_labels.values())
            nodes_processed = 0

            for label_str, nodes_list in nodes_by_labels.items():
                # Process in batches
                for i in range(0, len(nodes_list), batch_size):
                    batch = nodes_list[i : i + batch_size]

                    # Use UNWIND for batch insert - much more efficient
                    # Build the query dynamically based on properties
                    if batch:
                        prop_keys = list(batch[0].keys())
                        props_str = ", ".join(f"{k}: item.{k}" for k in prop_keys)

                        batch_query = f"""
                        UNWIND $batch AS item
                        CREATE (n:{label_str} {{{props_str}}})
                        """
                        try:
                            primary_ing._execute_query(batch_query, {"batch": batch})
                            job.nodes_synced += len(batch)
                            nodes_processed += len(batch)
                        except Exception as e:
                            logger.warning(
                                f"Batch insert failed, falling back to individual: {e}"
                            )
                            # Fallback to individual inserts
                            for node_props in batch:
                                try:
                                    single_props_str = ", ".join(
                                        f"{k}: ${k}" for k in node_props.keys()
                                    )
                                    single_query = (
                                        f"CREATE (n:{label_str} {{{single_props_str}}})"
                                    )
                                    primary_ing._execute_query(single_query, node_props)
                                    job.nodes_synced += 1
                                    nodes_processed += 1
                                except Exception as inner_e:
                                    logger.warning(f"Failed to create node: {inner_e}")

                    # Update progress
                    progress = (
                        50 + int((nodes_processed / total_nodes) * 25)
                        if total_nodes
                        else 75
                    )
                    job.progress = progress
                    job.message = f"Imported {job.nodes_synced}/{total_nodes} nodes..."

                    # Yield to allow read operations - critical for avoiding blocking
                    time.sleep(write_delay_ms / 1000.0)

            # Step 5: Import relationships to primary instance using UNWIND
            job.message = "Importing relationships to primary instance..."
            job.progress = 75

            # Group relationships by type for efficient batch inserts
            rels_by_type: dict[str, list] = {}
            for rel in rels:
                rel_type = rel["rel_type"]
                if rel_type not in rels_by_type:
                    rels_by_type[rel_type] = []
                rels_by_type[rel_type].append(
                    {
                        "from_qn": rel["from_qn"],
                        "to_qn": rel["to_qn"],
                        "props": rel["props"] or {},
                    }
                )

            total_rels = sum(len(r) for r in rels_by_type.values())
            rels_processed = 0

            for rel_type, rels_list in rels_by_type.items():
                for i in range(0, len(rels_list), batch_size):
                    batch = rels_list[i : i + batch_size]

                    # Use UNWIND for batch relationship creation
                    batch_query = f"""
                    UNWIND $batch AS item
                    MATCH (a {{qualified_name: item.from_qn}}), (b {{qualified_name: item.to_qn}})
                    CREATE (a)-[r:{rel_type}]->(b)
                    """
                    try:
                        # Simplify batch data (remove props for now, can be added if needed)
                        simple_batch = [
                            {"from_qn": r["from_qn"], "to_qn": r["to_qn"]}
                            for r in batch
                        ]
                        primary_ing._execute_query(batch_query, {"batch": simple_batch})
                        job.relationships_synced += len(batch)
                        rels_processed += len(batch)
                    except Exception as e:
                        logger.warning(
                            f"Batch relationship insert failed, falling back: {e}"
                        )
                        # Fallback to individual inserts
                        for rel_data in batch:
                            try:
                                from_qn = rel_data["from_qn"]
                                to_qn = rel_data["to_qn"]
                                single_query = f"""
                                MATCH (a {{qualified_name: $from_qn}}), (b {{qualified_name: $to_qn}})
                                CREATE (a)-[r:{rel_type}]->(b)
                                """
                                primary_ing._execute_query(
                                    single_query, {"from_qn": from_qn, "to_qn": to_qn}
                                )
                                job.relationships_synced += 1
                                rels_processed += 1
                            except Exception as inner_e:
                                logger.warning(
                                    f"Failed to create relationship: {inner_e}"
                                )

                    # Update progress
                    progress = (
                        75 + int((rels_processed / total_rels) * 20)
                        if total_rels
                        else 95
                    )
                    job.progress = progress
                    job.message = f"Imported {job.relationships_synced}/{total_rels} relationships..."

                    # Yield to allow read operations
                    time.sleep(write_delay_ms / 1000.0)

            # Step 6: Optionally clean build instance
            if clean_after_sync:
                job.message = "Cleaning build instance..."
                job.progress = 95
                build_ing.clean_project(project_name)
                logger.info(f"Cleaned project {project_name} from build instance")

            job.progress = 100

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status."""
        return {
            "current_sync": {
                "job_id": self._current_sync.job_id,
                "project_name": self._current_sync.project_name,
                "status": self._current_sync.status.value,
                "progress": self._current_sync.progress,
                "message": self._current_sync.message,
                "started_at": self._current_sync.started_at,
            }
            if self._current_sync
            else None,
            "recent_syncs": [
                {
                    "job_id": job.job_id,
                    "project_name": job.project_name,
                    "status": job.status.value,
                    "nodes_synced": job.nodes_synced,
                    "relationships_synced": job.relationships_synced,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "error": job.error,
                }
                for job in self._sync_history[-5:]
            ],
            "build_instance_configured": self.is_build_instance_configured(),
            "config": {
                "build_host": settings.MEMGRAPH_BUILD_HOST or "(not configured)",
                "build_port": settings.MEMGRAPH_BUILD_PORT,
                "sync_mode": settings.MEMGRAPH_BUILD_SYNC_MODE,
                "keep_after_sync": settings.MEMGRAPH_BUILD_KEEP_AFTER_SYNC,
            },
        }


# Global singleton instance
_sync_service: GraphSyncService | None = None


def get_graph_sync_service() -> GraphSyncService:
    """Get the global GraphSyncService instance."""
    global _sync_service
    if _sync_service is None:
        _sync_service = GraphSyncService()
    return _sync_service

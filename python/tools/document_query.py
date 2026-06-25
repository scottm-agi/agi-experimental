from __future__ import annotations
import asyncio

from python.helpers.tool import Tool, Response
from python.helpers.document_query import DocumentQueryHelper


class DocumentQueryTool(Tool):

    async def execute(self, **kwargs):
        document_uri = kwargs.get("document")
        document_uris = []

        if isinstance(document_uri, list):
            document_uris = document_uri
        elif isinstance(document_uri, str):
            document_uris = [document_uri]

        if not document_uris:
            return Response(message="Error: no document provided", break_loop=False)

        queries = (
            kwargs["queries"]
            if "queries" in kwargs
            else [kwargs["query"]]
            if ("query" in kwargs and kwargs["query"])
            else []
        )
        try:

            progress = []

            # logging callback
            def progress_callback(msg):
                progress.append(msg)
                self.log.update(progress="\n".join(progress))
            
            helper = DocumentQueryHelper(self.agent, progress_callback)
            if not queries:
                # Phase 3 hardening: asyncio.wait with timeout replaces bare asyncio.gather
                # to prevent network-bound document fetches from hanging the tool
                fetch_coros = [helper.document_get_content(uri) for uri in document_uris]
                futures = [asyncio.ensure_future(c) for c in fetch_coros]
                done, pending = await asyncio.wait(futures, timeout=120.0)
                if pending:
                    for p in pending:
                        p.cancel()
                    await asyncio.wait(pending, timeout=5.0)
                contents = []
                for f in futures:
                    if f in done and not f.cancelled():
                        try:
                            contents.append(f.result())
                        except Exception as e:
                            contents.append(f"[Error fetching document: {e}]")
                    else:
                        contents.append("[Document fetch timed out after 120s]")
                content = "\n\n---\n\n".join(contents)
            else:
                _, content = await helper.document_qa(document_uris, queries)
            return Response(message=content, break_loop=False)
        except Exception as e:  # pylint: disable=broad-exception-caught
            return Response(message=f"Error processing document: {e}", break_loop=False)

import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from python.helpers.task_scheduler import TaskScheduler

async def trigger():
    uuid = "29807477-b833-4b5a-a0c4-4dd8a71d26b5"
    print(f"Triggering task {uuid}...")
    scheduler = TaskScheduler.get()
    await scheduler.reload()
    try:
        await scheduler.run_task_by_uuid(uuid)
        print("Task run initiated (or completed if synchronous).")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error triggering task: {e}")

if __name__ == "__main__":
    asyncio.run(trigger())

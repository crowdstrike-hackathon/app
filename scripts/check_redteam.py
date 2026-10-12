"""Live two-round smoke check using the configured models and sandbox tools only."""
import asyncio
import json

from app.agent import runtime
from app.engine.redteam import Tournament


async def main():
    runtime.load()
    run = Tournament("smoke-check", 2, "refund_over_limit")
    await run.run()
    snapshot = run.snapshot()
    print(json.dumps({"status": run.status, "error": run.error, "tally": snapshot["tally"],
                      "rounds": [{"number": row["number"], "results": {
                          mode: {k: result[k] for k in ("success", "valid", "outcome", "stats")}
                          for mode, result in row["results"].items()}}
                          for row in run.rounds]}, indent=2))
    if run.status != "done" or any(not r["valid"] for row in run.rounds for r in row["results"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

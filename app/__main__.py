"""Start the site the way the deployment does: ``python -m app``.

The host and the port come from the environment, which is how a platform hands
them to a process -- Render, for instance, assigns ``PORT`` and expects the app
to bind ``0.0.0.0``. Locally nothing is set, so it comes up on
``http://localhost:8011`` (see README.md; 8000 is a busy default and 8011 is not).

One worker, on purpose: the rate limits and the login throttles are in-process
counters (app.services.rate_limit), so a second worker would mean two independent
budgets and limits that are twice as loose as they read.
"""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8011")),
        workers=1,
    )


if __name__ == "__main__":
    main()

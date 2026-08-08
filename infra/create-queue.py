"""Idempotently create the walker->processor work queue in Azurite (infra ops, ADR-0020).

Neither ``MessageQueue`` nor Azurite auto-creates the queue, and the application's
production queue semantics are deliberately left unchanged, so the live-fire compose
stack creates it once via this dedicated init step — the queue counterpart of the
one-time ``alembic upgrade head`` migrate step.

This is standalone infrastructure tooling (it runs from a bind-mount, outside
``src/``), so it reads the same ``CLASSIFIER__QUEUE_*`` environment the app uses
directly rather than importing the ``Settings`` model. It retries while Azurite is
still coming up, and treats an already-existing queue as success so re-runs are safe.
"""

import os
import time

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.queue import QueueClient

_MAX_ATTEMPTS = 30
_RETRY_SECONDS = 2


def main() -> None:
    connection_string = os.environ["CLASSIFIER__QUEUE_CONNECTION_STRING"]
    name = os.environ["CLASSIFIER__QUEUE_NAME"]
    client = QueueClient.from_connection_string(connection_string, name)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            client.create_queue()
            print(f"Created queue {name!r}")
            return
        except ResourceExistsError:
            print(f"Queue {name!r} already exists")
            return
        except AzureError as err:
            print(f"Attempt {attempt}/{_MAX_ATTEMPTS}: Azurite not ready yet ({err}); retrying...")
            time.sleep(_RETRY_SECONDS)

    raise SystemExit(f"Timed out waiting for Azurite to accept creation of queue {name!r}")


if __name__ == "__main__":
    main()

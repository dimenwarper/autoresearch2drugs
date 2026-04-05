from __future__ import annotations

import json

from .example_agent import run_example_policy


if __name__ == "__main__":
    result = run_example_policy()
    print(json.dumps(result, indent=2, sort_keys=True))

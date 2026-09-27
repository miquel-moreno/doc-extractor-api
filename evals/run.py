"""Evaluation entry point (`make eval`).

Runs against a real LLM, so it is local only and never part of CI.
Each project replaces this with its own dataset and metrics, and writes
results to evals/results/<date>_<model>.json.
"""


def main() -> None:
    print("No evaluation defined yet for this project.")


if __name__ == "__main__":
    main()

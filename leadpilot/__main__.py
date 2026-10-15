"""`python -m leadpilot` starts the API + worker on $PORT (default 8000)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run("leadpilot.api:create_app", factory=True, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()

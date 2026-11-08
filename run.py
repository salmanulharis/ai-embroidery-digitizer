"""Start the web app:  python run.py  ->  http://127.0.0.1:8000"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "embroidery_app.api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("RELOAD", "0") == "1",
    )

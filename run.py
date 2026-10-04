"""Start the CentrAlign AI prototype:  python run.py"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    os.environ.setdefault("FINANCE_API_URL", f"http://127.0.0.1:{port}")
    print(f"\n  CentrAlign AI dashboard → http://localhost:{port}\n")
    uvicorn.run("app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=port, log_level="info")

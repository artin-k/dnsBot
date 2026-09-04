# run_web_ip_updater.py
from app.services.controld import get_controld_device_ips  # Re-export safety bridge

# run_web_ip_updater.py
"""
Production Entrypoint for Uvicorn on your VPS.
Re-exports the canonical FastAPI application from ip_server.
"""
from ip_server import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run_web_ip_updater:app", host="0.0.0.0", port=8000, reload=False)
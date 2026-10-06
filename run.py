import os
import sys
import time
import subprocess
import httpx
import logging

# Ensure project root is in python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from src.config import BACKEND_HOST, BACKEND_PORT, BACKEND_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("orchestrator")

def check_backend_alive() -> bool:
    """Helper to check if FastAPI backend is responding."""
    try:
        response = httpx.get(f"{BACKEND_URL}/projects", timeout=1.0)
        return response.status_code == 200
    except Exception:
        return False

def main():
    logger.info("Starting AI Novel Condenser & Audiobook Generator...")
    
    # Path to virtual env python and executables
    if sys.platform == "win32":
        python_exe = os.path.join("venv", "Scripts", "python.exe")
        streamlit_exe = os.path.join("venv", "Scripts", "streamlit.exe")
        uvicorn_exe = os.path.join("venv", "Scripts", "uvicorn.exe")
    else:
        python_exe = os.path.join("venv", "bin", "python")
        streamlit_exe = os.path.join("venv", "bin", "streamlit")
        uvicorn_exe = os.path.join("venv", "bin", "uvicorn")

    # Fallback to system python if venv isn't set up yet (though it should be)
    if not os.path.exists(python_exe):
        logger.warning("Virtual environment executables not found. Falling back to system environment.")
        python_exe = "python"
        streamlit_exe = "streamlit"
        uvicorn_exe = "uvicorn"

    backend_process = None
    frontend_process = None

    try:
        # 1. Start FastAPI Backend
        logger.info(f"Launching FastAPI backend on http://{BACKEND_HOST}:{BACKEND_PORT}...")
        backend_cmd = [
            python_exe, "-m", "uvicorn", "src.backend:app",
            "--host", BACKEND_HOST,
            "--port", str(BACKEND_PORT)
        ]
        backend_process = subprocess.Popen(
            backend_cmd,
            stdout=None,  # Output directly to user console
            stderr=None
        )

        # 2. Wait for backend to activate
        logger.info("Waiting for database initialization and backend startup...")
        backend_ready = False
        for i in range(15):
            time.sleep(1.0)
            if check_backend_alive():
                backend_ready = True
                break
            
            # Check if process terminated prematurely
            if backend_process.poll() is not None:
                logger.error("FastAPI backend failed to start. See console logs.")
                sys.exit(1)
                
        if not backend_ready:
            logger.error("FastAPI backend is taking too long to start. Attempting to start frontend anyway...")
        else:
            logger.info("Backend is online!")

        # 3. Start Streamlit Frontend
        logger.info("Launching Streamlit frontend...")
        frontend_cmd = [
            streamlit_exe, "run", "src/frontend.py",
            "--server.port", "8501",
            "--server.address", "127.0.0.1"
        ]
        
        # Streamlit writes to stdout directly
        frontend_process = subprocess.Popen(
            frontend_cmd,
            stdout=None,
            stderr=None
        )

        logger.info("System fully operational. Press Ctrl+C to terminate.")
        
        # Keep orchestrator running, check on subprocesses
        while True:
            time.sleep(2.0)
            
            # If backend or frontend died, shutdown everything
            if backend_process.poll() is not None:
                logger.error("FastAPI backend stopped unexpectedly.")
                break
            if frontend_process.poll() is not None:
                logger.error("Streamlit frontend stopped unexpectedly.")
                break

    except KeyboardInterrupt:
        logger.info("Interrupt received, shutting down subprocesses...")
    except Exception as e:
        logger.exception(f"Unexpected error in orchestrator: {str(e)}")
    finally:
        # Graceful shutdown of servers
        if frontend_process:
            logger.info("Terminating Streamlit frontend...")
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Frontend did not exit. Forcing shutdown...")
                frontend_process.kill()

        if backend_process:
            logger.info("Terminating FastAPI backend...")
            backend_process.terminate()
            try:
                backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Backend did not exit. Forcing shutdown...")
                backend_process.kill()
                
        logger.info("Shutdown completed.")

if __name__ == "__main__":
    main()

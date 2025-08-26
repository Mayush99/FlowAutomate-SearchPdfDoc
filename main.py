import uvicorn
from api import app
from config import API_HOST, API_PORT

if __name__ == "__main__":
    print("Starting PDF Search Engine API...")
    print(f"Server will be available at: http://{API_HOST}:{API_PORT}")
    print(f"API Documentation: http://{API_HOST}:{API_PORT}/docs")
    print(f"ReDoc Documentation: http://{API_HOST}:{API_PORT}/redoc")
    
    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=API_PORT,
        reload=True,
        log_level="info",
        access_log=True
    )

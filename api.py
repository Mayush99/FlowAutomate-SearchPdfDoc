from fastapi import FastAPI, HTTPException, Depends, status, Request, File, UploadFile, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import json
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime, timedelta
import asyncio
import aiofiles

from models import (
    PDFDocument, SearchQuery, SearchResponse, UserCreate, User, Token,
    ContentType
)
from elasticsearch_client import ElasticsearchClient
from etl_pipeline import PDFDataProcessor, DataValidator
from auth import UserManager, JWTManager, RateLimiter, SecurityUtils
from config import (
    API_HOST, API_PORT, CORS_ORIGINS, RATE_LIMIT_REQUESTS, 
    RATE_LIMIT_WINDOW, MAX_FILE_SIZE
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="PDF Search Engine API",
    description="Secure API for searching parsed PDF content using Elasticsearch",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Security
security = HTTPBearer()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Add trusted host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "*.localhost"]
)

# Initialize components
es_client = ElasticsearchClient()
etl_processor = PDFDataProcessor(es_client)
user_manager = UserManager()
jwt_manager = JWTManager()
rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response

# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Get client identifier
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "")
    client_id = SecurityUtils.hash_client_id(client_ip, user_agent)
    
    # Check rate limit
    if not rate_limiter.is_allowed(client_id):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please try again later."},
            headers={"Retry-After": "3600"}
        )
    
    response = await call_next(request)
    
    # Add rate limit headers
    remaining = rate_limiter.get_remaining_requests(client_id)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(int((datetime.now() + timedelta(seconds=RATE_LIMIT_WINDOW)).timestamp()))
    
    return response

# Dependency to get current user
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token_data = jwt_manager.verify_token(credentials.credentials)
        if token_data is None or token_data.username is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception
    
    user = user_manager.get_user(username=token_data.username)
    if user is None or not user.is_active:
        raise credentials_exception
    
    return user

# Authentication endpoints
@app.post("/auth/register", response_model=Dict[str, str])
async def register_user(user: UserCreate):
    """Register a new user"""
    try:
        created_user = user_manager.create_user(user)
        if not created_user:
            raise HTTPException(
                status_code=400,
                detail="Username or email already registered"
            )
        
        logger.info(f"New user registered: {user.username}")
        return {"message": "User registered successfully", "username": user.username}
        
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")

@app.post("/auth/login", response_model=Token)
async def login_user(username: str = Form(...), password: str = Form(...)):
    """Login and get access token"""
    try:
        user = user_manager.authenticate_user(username, password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated"
            )
        
        access_token_expires = timedelta(minutes=30)
        access_token = jwt_manager.create_access_token(
            data={"sub": user.username}, expires_delta=access_token_expires
        )
        
        logger.info(f"User logged in: {username}")
        return {"access_token": access_token, "token_type": "bearer"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise HTTPException(status_code=500, detail="Login failed")

# Search endpoints
@app.get("/search", response_model=SearchResponse)
async def search_documents(
    q: str,
    content_types: Optional[str] = None,
    page_numbers: Optional[str] = None,
    document_ids: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    current_user: User = Depends(get_current_user)
):
    """Search across all indexed documents"""
    try:
        # Sanitize search query
        sanitized_query = SecurityUtils.sanitize_search_query(q)
        if not sanitized_query:
            raise HTTPException(status_code=400, detail="Invalid search query")
        
        # Parse optional parameters
        parsed_content_types = None
        if content_types:
            try:
                parsed_content_types = [
                    ContentType(ct.strip()) for ct in content_types.split(',')
                ]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid content types")
        
        parsed_page_numbers = None
        if page_numbers:
            try:
                parsed_page_numbers = [
                    int(p.strip()) for p in page_numbers.split(',')
                ]
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid page numbers")
        
        parsed_document_ids = None
        if document_ids:
            parsed_document_ids = [d.strip() for d in document_ids.split(',')]
        
        # Create search query
        search_query = SearchQuery(
            query=sanitized_query,
            content_types=parsed_content_types,
            page_numbers=parsed_page_numbers,
            document_ids=parsed_document_ids,
            limit=min(limit, 100),  # Enforce max limit
            offset=max(offset, 0)   # Ensure non-negative offset
        )
        
        # Execute search
        results = es_client.search_documents(search_query)
        
        logger.info(f"Search by {current_user.username}: '{sanitized_query}' - {results.total_hits} hits")
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail="Search failed")

@app.post("/search", response_model=SearchResponse)
async def advanced_search(
    search_query: SearchQuery,
    current_user: User = Depends(get_current_user)
):
    """Advanced search with full query options"""
    try:
        # Sanitize search query
        sanitized_query = SecurityUtils.sanitize_search_query(search_query.query)
        search_query.query = sanitized_query
        
        if not sanitized_query:
            raise HTTPException(status_code=400, detail="Invalid search query")
        
        # Execute search
        results = es_client.search_documents(search_query)
        
        logger.info(f"Advanced search by {current_user.username}: '{sanitized_query}' - {results.total_hits} hits")
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Advanced search failed: {e}")
        raise HTTPException(status_code=500, detail="Advanced search failed")

# Health and monitoring endpoints
@app.get("/health", response_model=Dict[str, Any])
async def health_check():
    """Health check endpoint"""
    try:
        es_health = es_client.health_check()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "elasticsearch": es_health,
            "api_version": "1.0.0"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
            "api_version": "1.0.0"
        }

@app.get("/metrics", response_model=Dict[str, Any])
async def get_metrics(current_user: User = Depends(get_current_user)):
    """Get API metrics (authenticated endpoint)"""
    try:
        es_health = es_client.health_check()
        
        return {
            "elasticsearch": {
                "status": es_health.get("status", "unknown"),
                "documents": es_health.get("documents_count", 0),
                "index_size_bytes": es_health.get("index_size", 0)
            },
            "api": {
                "version": "1.0.0",
                "uptime": "calculated_at_runtime"
            },
            "user": {
                "username": current_user.username,
                "requests_remaining": rate_limiter.get_remaining_requests(
                    SecurityUtils.hash_client_id("system", "metrics")
                )
            }
        }
    except Exception as e:
        logger.error(f"Metrics failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get metrics")

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": "Endpoint not found"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=API_PORT,
        reload=True,
        log_level="info"
    )

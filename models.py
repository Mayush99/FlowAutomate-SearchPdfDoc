from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class ContentType(str, Enum):
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    TABLE = "table"

class ParsedContent(BaseModel):
    content_type: ContentType
    content: str
    page_number: int
    position: Dict[str, float]  # x, y, width, height
    metadata: Optional[Dict[str, Any]] = None

class PDFDocument(BaseModel):
    document_id: str = Field(..., description="Unique identifier for the document")
    filename: str
    file_path: str
    total_pages: int
    parsed_content: List[ParsedContent]
    upload_timestamp: datetime = Field(default_factory=datetime.now)
    file_size: int
    checksum: str

class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    content_types: Optional[List[ContentType]] = None
    page_numbers: Optional[List[int]] = None
    document_ids: Optional[List[str]] = None
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

class SearchResult(BaseModel):
    document_id: str
    filename: str
    content_type: ContentType
    content: str
    page_number: int
    score: float
    highlight: Optional[str] = None

class SearchResponse(BaseModel):
    results: List[SearchResult]
    total_hits: int
    query_time_ms: float
    page: int
    per_page: int

class User(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., regex=r'^[^@]+@[^@]+\.[^@]+$')
    full_name: Optional[str] = None

class UserCreate(User):
    password: str = Field(..., min_length=8)

class UserInDB(User):
    id: int
    hashed_password: str
    created_at: datetime
    is_active: bool = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    username: Optional[str] = None

import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Optional
import logging
from passlib.context import CryptContext
from jose import JWTError, jwt

from models import UserCreate, UserInDB, TokenData
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserManager:
    """Manage user authentication and authorization"""
    
    def __init__(self):
        self.db_path = DATABASE_URL.replace("sqlite:///", "")
        self._init_database()
    
    def _init_database(self):
        """Initialize the user database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    full_name TEXT,
                    hashed_password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("User database initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize user database: {e}")
            raise
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(plain_password, hashed_password)
    
    def get_password_hash(self, password: str) -> str:
        """Hash a password"""
        return pwd_context.hash(password)
    
    def create_user(self, user: UserCreate) -> Optional[UserInDB]:
        """Create a new user"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if user already exists
            cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", 
                         (user.username, user.email))
            if cursor.fetchone():
                logger.warning(f"User already exists: {user.username}")
                conn.close()
                return None
            
            # Hash password and insert user
            hashed_password = self.get_password_hash(user.password)
            cursor.execute('''
                INSERT INTO users (username, email, full_name, hashed_password)
                VALUES (?, ?, ?, ?)
            ''', (user.username, user.email, user.full_name, hashed_password))
            
            user_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Return created user
            return self.get_user_by_id(user_id)
            
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return None
    
    def get_user(self, username: str) -> Optional[UserInDB]:
        """Get user by username"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, full_name, hashed_password, created_at, is_active
                FROM users WHERE username = ?
            ''', (username,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return UserInDB(
                    id=row['id'],
                    username=row['username'],
                    email=row['email'],
                    full_name=row['full_name'],
                    hashed_password=row['hashed_password'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    is_active=bool(row['is_active'])
                )
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user {username}: {e}")
            return None
    
    def get_user_by_id(self, user_id: int) -> Optional[UserInDB]:
        """Get user by ID"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, username, email, full_name, hashed_password, created_at, is_active
                FROM users WHERE id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return UserInDB(
                    id=row['id'],
                    username=row['username'],
                    email=row['email'],
                    full_name=row['full_name'],
                    hashed_password=row['hashed_password'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    is_active=bool(row['is_active'])
                )
            return None
            
        except Exception as e:
            logger.error(f"Failed to get user by ID {user_id}: {e}")
            return None
    
    def authenticate_user(self, username: str, password: str) -> Optional[UserInDB]:
        """Authenticate a user"""
        user = self.get_user(username)
        if not user:
            return None
        if not self.verify_password(password, user.hashed_password):
            return None
        return user
    
    def deactivate_user(self, username: str) -> bool:
        """Deactivate a user account"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("UPDATE users SET is_active = 0 WHERE username = ?", (username,))
            updated = cursor.rowcount > 0
            
            conn.commit()
            conn.close()
            
            if updated:
                logger.info(f"Deactivated user: {username}")
            return updated
            
        except Exception as e:
            logger.error(f"Failed to deactivate user {username}: {e}")
            return False

class JWTManager:
    """Manage JWT tokens for authentication"""
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
        """Create a JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def verify_token(token: str) -> Optional[TokenData]:
        """Verify and decode a JWT token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                return None
            return TokenData(username=username)
        except JWTError:
            return None

class RateLimiter:
    """Simple in-memory rate limiter"""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 3600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = {}  # {client_id: [timestamps]}
    
    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed for client"""
        now = datetime.now()
        
        # Clean old requests
        if client_id in self.requests:
            self.requests[client_id] = [
                timestamp for timestamp in self.requests[client_id]
                if (now - timestamp).total_seconds() < self.window_seconds
            ]
        else:
            self.requests[client_id] = []
        
        # Check rate limit
        if len(self.requests[client_id]) >= self.max_requests:
            return False
        
        # Add current request
        self.requests[client_id].append(now)
        return True
    
    def get_remaining_requests(self, client_id: str) -> int:
        """Get remaining requests for client"""
        if client_id not in self.requests:
            return self.max_requests
        
        now = datetime.now()
        valid_requests = [
            timestamp for timestamp in self.requests[client_id]
            if (now - timestamp).total_seconds() < self.window_seconds
        ]
        
        return max(0, self.max_requests - len(valid_requests))

# Security utilities
class SecurityUtils:
    """Additional security utilities"""
    
    @staticmethod
    def hash_client_id(ip_address: str, user_agent: str) -> str:
        """Create a hash for rate limiting based on IP and user agent"""
        combined = f"{ip_address}:{user_agent}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    @staticmethod
    def sanitize_search_query(query: str) -> str:
        """Sanitize search query to prevent injection attacks"""
        # Remove potentially dangerous characters
        dangerous_chars = ['<', '>', '"', "'", '&', ';', '(', ')', '|', '`']
        sanitized = query
        for char in dangerous_chars:
            sanitized = sanitized.replace(char, '')
        
        # Limit length
        return sanitized[:500].strip()
    
    @staticmethod
    def validate_file_upload(filename: str, content_type: str, file_size: int) -> tuple[bool, str]:
        """Validate file upload parameters"""
        # Check file extension
        allowed_extensions = {'.pdf', '.json'}
        file_ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
        if file_ext not in allowed_extensions:
            return False, f"File extension {file_ext} not allowed"
        
        # Check content type
        allowed_content_types = {
            'application/pdf',
            'application/json',
            'text/plain'
        }
        if content_type not in allowed_content_types:
            return False, f"Content type {content_type} not allowed"
        
        # Check file size (100MB limit)
        max_size = 100 * 1024 * 1024
        if file_size > max_size:
            return False, f"File size {file_size} exceeds limit of {max_size} bytes"
        
        return True, "Valid file"
